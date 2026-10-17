from app import tools


def reminder_text(chat_id: int):
    r = tools.deadlines(chat_id)
    if "error" in r or not r["procedures"]:
        return None
    p = r["procedures"][0]
    return (f"Reminder: {p['name_en']} is due {p['due']} ({p['days_left']} days left). "
            "Send /plan to see your steps.")
