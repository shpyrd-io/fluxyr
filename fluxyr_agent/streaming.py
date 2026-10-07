"""Persist displayable provider stream blocks for live UI and reconnect replay."""

import uuid


def field(value, key, default=None):
    return (
        value.get(key, default)
        if isinstance(value, dict)
        else getattr(value, key, default)
    )


class StreamRecorder:
    def __init__(self, emit):
        self.emit = emit
        self.blocks = {}
        self.tools = {}
        self.wire_arguments = False

    def _open(self, index, kind):
        block = {"block_id": str(uuid.uuid4()), "kind": kind}
        self.blocks[index] = block
        self.emit("stream_open", block)
        return block

    def __call__(self, event, *_):
        kind = field(event, "type")
        index = field(event, "index", 0)
        if kind == "provider_tool_delta":
            self.wire_arguments = True
            self.emit(
                "tool_argument_delta",
                {
                    "tool_name": field(event, "tool_name"),
                    "tool_call_id": field(event, "tool_call_id"),
                    "characters": field(event, "characters", 0),
                },
            )
            return
        if kind == "content_block_start":
            content = field(event, "content_block")
            channel = field(content, "type")
            if channel == "tool_use":
                tool = {
                    "block_id": str(uuid.uuid4()),
                    "tool_name": field(content, "name", ""),
                    "tool_call_id": field(content, "id"),
                }
                self.tools[index] = tool
                self.emit("tool_stream_open", tool)
                return
            if channel not in ("text", "thinking"):
                return
            block = self._open(index, "reasoning" if channel == "thinking" else "delta")
            text = field(content, "thinking" if channel == "thinking" else "text", "")
            if text:
                self.emit(block["kind"], {**block, "text": text})
        elif kind == "content_block_delta":
            delta = field(event, "delta")
            channel = field(delta, "type")
            if channel == "input_json_delta":
                tool = self.tools.get(index, {})
                fragment = field(delta, "partial_json", "")
                if fragment and not self.wire_arguments:
                    self.emit(
                        "tool_argument_delta", {**tool, "characters": len(fragment)}
                    )
                return
            if channel == "thinking_delta":
                text, event_kind = field(delta, "thinking"), "reasoning"
            else:
                text, event_kind = field(delta, "text"), "delta"
            if text:
                block = self.blocks.get(index) or self._open(index, event_kind)
                self.emit(event_kind, {**block, "text": text})
        elif kind == "content_block_stop":
            tool = self.tools.pop(index, None)
            if tool:
                self.emit("tool_stream_close", tool)
            block = self.blocks.pop(index, None)
            if block:
                self.emit("stream_close", block)
