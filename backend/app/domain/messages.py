"""Customer-facing recovery message templates (English and Hinglish)."""


def format_inr(amount: int) -> str:
    return f"₹{amount:,}"


def compose_recovery_message(customer_name: str, amount: int, language: str, link_url: str) -> str:
    """Outreach asking the customer to pay through a provider-created link. Never invent the URL."""
    first_name = customer_name.split()[0] if customer_name else "there"
    formatted_amount = format_inr(amount)
    if language == "Hinglish":
        return (
            f"Hi {first_name} 👋\n\n"
            f"Aapka {formatted_amount} payment complete nahi ho paya.\n\n"
            "Aap neeche diye secure link se turant payment complete kar sakte hain:\n"
            f"{link_url}\n\n"
            "Agar koi issue hai toh reply karein. — RecoverX"
        )
    return (
        f"Hi {first_name} 👋\n\n"
        f"Your payment of {formatted_amount} could not be completed.\n\n"
        "You can securely complete your payment using the link below:\n"
        f"{link_url}\n\n"
        "Need assistance? Reply to this message. — RecoverX"
    )
