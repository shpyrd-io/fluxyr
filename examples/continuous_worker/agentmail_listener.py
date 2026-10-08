"""Requires agentmail and declared Vault item 'AgentMail API key' (text)."""
from agentmail import AgentMail, MessageReceivedEvent, Subscribe, Subscribed

INBOX_ID = "your-inbox@agentmail.to"


def run(ctx):
    client = AgentMail(api_key=ctx.secret("AgentMail API key")["value"])
    with client.websockets.connect() as socket:
        socket.send_subscribe(
            Subscribe(inbox_ids=[INBOX_ID], event_types=["message.received"])
        )
        for event in socket:
            if ctx.stopping:
                break
            if isinstance(event, Subscribed):
                ctx.ready()
            elif isinstance(event, MessageReceivedEvent):
                message = event.message
                ctx.emit(
                    session_key=f"{message.inbox_id}:{message.thread_id}",
                    event_id=f"{message.inbox_id}:{message.message_id}",
                    payload={
                        "inbox_id": message.inbox_id,
                        "thread_id": message.thread_id,
                        "message_id": message.message_id,
                        "from": message.from_,
                        "subject": (message.subject or "")[:1000],
                        "text": (message.text or "")[:16000],
                        "text_truncated": len(message.text or "") > 16000,
                    },
                )
