import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from app import agent, documents, engine, graph, nams, privacy, reminders, store, tools, vault

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "httpx2", "httpcore"):  # their INFO logs print request URLs, which contain the bot token
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("kiku")

WELCOME = (
    "Kiku (聞く): government paperwork in Japan, one step at a time.\n\n"
    "Tap a demo persona, or describe your situation in your own words "
    "(visa type, age, where you moved from, move-in date, employer insurance, children).\n\n"
    "I can build your paperwork plan, and I can look things up on official Japanese government sites. "
    "Office details exist only for " + engine.local_ward_names() + "; other areas get national steps to confirm locally.\n\n"
    "You can also send a photo or PDF of your residence card: I read it on this computer, show what I found and save only after you tap Save.\n\n"
    "To help you better I remember your name, situation and goals (never passport, residence card or My Number). "
    "{memory_line} /memory shows what I know; /forget deletes it all.\n\n"
    "This is a demo: general guidance, not legal advice."
)
COMMANDS = [
    BotCommand("start", "Start or pick a demo persona"),
    BotCommand("plan", "Show my plan"),
    BotCommand("done", "Mark a step done"),
    BotCommand("memory", "Show what I remember about you"),
    BotCommand("remind", "Demo: send a deadline reminder in 20 seconds"),
    BotCommand("forget", "Delete my data"),
]
LIMIT = 4000  # Telegram caps a message at 4096 characters


async def reply(message, text: str, **kw):
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > LIMIT:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for i, c in enumerate(chunks):
        await message.reply_text(c.rstrip() or "-", **(kw if i == len(chunks) - 1 else {}))


async def _next_keyboard(chat_id: int):
    r = await asyncio.to_thread(tools.next_steps, chat_id)
    items = r.get("procedures", [])
    rows = [[InlineKeyboardButton(f"Done: {p['name_en'][:40]}", callback_data=f"done:{p['id']}")]
            for p in items[:5]]
    return (InlineKeyboardMarkup(rows) if rows else None), r.get("source")


async def _show_plan(message, chat_id: int):
    await reply(message, await asyncio.to_thread(tools.format_plan, chat_id))
    kb, source = await _next_keyboard(chat_id)
    if kb:
        label = "Do next (checked against the Neo4j graph):" if source == "neo4j" else "Do next:"
        await message.reply_text(label, reply_markup=kb)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(p["label"], callback_data=f"persona:{p['id']}")] for p in tools.personas()]
    line = ("Your messages are stored in Neo4j's hosted agent-memory service so I can recall earlier chats."
            if nams.ENABLED else "Recent messages are kept in memory only.")
    await update.message.reply_text(WELCOME.replace("{memory_line}", line), reply_markup=InlineKeyboardMarkup(kb))


async def plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _show_plan(update.effective_message, update.effective_chat.id)


async def done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if ctx.args:
        r = await asyncio.to_thread(tools.mark_done, chat_id, ctx.args[0])
        if "error" in r and "unknown" in r["error"]:
            await update.message.reply_text("I do not know that step. Send /done to pick one.")
            return
        await _show_plan(update.effective_message, chat_id)
    else:
        kb, _ = await _next_keyboard(chat_id)
        await update.message.reply_text("Which one did you finish?" if kb else "Nothing to mark yet. Send /start first.",
                                        reply_markup=kb)


async def memory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await reply(update.message, await asyncio.to_thread(tools.memory_summary, update.effective_chat.id))


async def forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    r = await asyncio.to_thread(tools.forget, update.effective_chat.id)
    if r["database_cleared"] is False or r["memory_service_cleared"] is False:
        await update.message.reply_text("I cleared this chat, but could not reach a storage service to delete all your "
                                        "saved data. Please send /forget again in a minute.")
        return
    await update.message.reply_text("Done. I deleted your profile and progress. Send /start to begin again.")


async def _fire_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    text = await asyncio.to_thread(reminders.reminder_text, ctx.job.chat_id)
    if text:
        await ctx.bot.send_message(ctx.job.chat_id, text)


async def remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not await asyncio.to_thread(reminders.reminder_text, chat_id):
        await update.message.reply_text("No open deadline yet. Send /start and pick a persona first.")
        return
    if ctx.job_queue is None:
        await update.message.reply_text("Reminders are not available (job queue not installed).")
        return
    ctx.job_queue.run_once(_fire_reminder, 20, chat_id=chat_id, name=f"remind-{chat_id}")
    await update.message.reply_text("OK. I will remind you about your nearest deadline in 20 seconds (demo timer).")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kind, _, value = q.data.partition(":")
    chat_id = q.message.chat_id
    if kind == "persona":
        await asyncio.to_thread(tools.load_persona, chat_id, value)
    elif kind == "doc":
        if value in ("save", "savevault"):
            applied = await asyncio.to_thread(tools.apply_document, chat_id, value == "savevault")
            if applied is None:
                await q.message.reply_text("That one expired. Please send the file again.")
                return
            await q.message.reply_text("Saved. I updated: " + ", ".join(sorted(applied)) + ".")
        else:
            await asyncio.to_thread(tools.discard_document, chat_id)
            await q.message.reply_text("OK, I did not save anything.")
            return
    elif kind == "done":
        r = await asyncio.to_thread(tools.mark_done, chat_id, value)
        if r.get("unlocked"):
            names = "\n".join(f"- {p['name_en']}" for p in r["unlocked"])
            await q.message.reply_text(f"Done. This unlocked:\n{names}")
    await _show_plan(q.message, chat_id)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    clean, removed = privacy.redact(update.message.text)
    if removed:  # take the number out of the chat itself and tell the user
        try:
            await update.message.delete()
        except Exception:
            pass
        await ctx.bot.send_message(chat_id, "I removed an ID-like number from your message and did not store it. "
                                            "I never need passport, residence card or My Number numbers.")
    loop = asyncio.get_running_loop()

    def status(msg: str):  # called from the worker thread while the agent researches
        asyncio.run_coroutine_threadsafe(update.message.reply_text(msg), loop)

    async def typing():
        while True:
            await ctx.bot.send_chat_action(chat_id, "typing")
            await asyncio.sleep(4)

    keepalive = asyncio.create_task(typing())
    try:
        text = await asyncio.to_thread(agent.run_agent, chat_id, clean, status)
    finally:
        keepalive.cancel()
    await reply(update.message, text)


class _ViaChat:
    """Lets `reply` send to a chat when the message it would answer has been deleted."""
    def __init__(self, chat):
        self.chat = chat

    async def reply_text(self, text, **kw):
        return await self.chat.send_message(text, **kw)


async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg, chat_id = update.message, update.effective_chat.id
    if msg.photo:
        f, name, mime = msg.photo[-1], "photo.jpg", "image/jpeg"
    else:
        f, name, mime = msg.document, msg.document.file_name or "", msg.document.mime_type or ""
    if f.file_size and f.file_size > documents.MAX_BYTES:
        await msg.reply_text("That file is too large (limit 10 MB).")
        return
    await msg.reply_text("Reading your file on this computer...")
    data = bytes(await (await ctx.bot.get_file(f.file_id)).download_as_bytearray())
    try:  # the card number is visible in the file: take it out of the chat
        await msg.delete()
    except Exception:
        pass
    try:
        result = await asyncio.to_thread(documents.process, data, name, mime)
    except documents.DocumentError as e:
        await msg.chat.send_message(str(e) + " (A photo sent as a file is usually easier to read than a compressed photo.)")
        return
    finally:
        del data
    await asyncio.to_thread(tools.stage_document, chat_id, result)
    rows = [[InlineKeyboardButton("Save these", callback_data="doc:save")]]
    if result.get("personal") and vault.available():
        rows.append([InlineKeyboardButton("Save + encrypted personal details", callback_data="doc:savevault")])
    rows.append([InlineKeyboardButton("Not now", callback_data="doc:cancel")])
    kb = InlineKeyboardMarkup(rows)
    await reply(_ViaChat(msg.chat), await asyncio.to_thread(tools.describe_document, chat_id, result), reply_markup=kb)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("handler error", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Sorry, something went wrong. Please try again.")


async def post_init(app: Application):
    await app.bot.set_my_commands(COMMANDS)


def main():
    if graph.enable():
        store.init_schema()
        log.info("Neo4j enabled: progress is saved per chat and next steps are checked against the graph")
    else:
        log.warning("Neo4j NOT enabled: progress is kept in memory only")
    if nams.enable():
        log.info("Agent memory service enabled: earlier messages are recalled before each reply")
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plan", plan))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("memory", memory))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.PDF | filters.Document.IMAGE, on_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    log.info("Kiku is running (long polling). Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
