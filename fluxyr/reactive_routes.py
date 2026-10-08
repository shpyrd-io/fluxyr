"""HTTP receiver and routine inbox management. No model runs in the receiver."""

import json

from flask import jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from .reactive import MAX_BYTES


def register_reactive_routes(routes, engine):
    reactive = engine.reactive

    @routes.post("/api/webhooks/<token>")
    def receive_webhook(token):
        if request.content_length and request.content_length > MAX_BYTES:
            raise RequestEntityTooLarge("Webhook body exceeds 256 KiB")
        raw = request.stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise RequestEntityTooLarge("Webhook body exceeds 256 KiB")
        rid = reactive.authorize(
            token, raw, request.headers.get("X-Webhook-Signature", "")
        )
        # Cache for Werkzeug's form decoder after the bounded raw read.
        request._cached_data = raw
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise BadRequest(
                "Webhook body must be UTF-8; send file references instead of binary uploads"
            ) from None
        if request.is_json:
            try:
                data = json.loads(text)
            except ValueError:
                raise BadRequest("Invalid JSON body") from None
            envelope = {"json": data, "text": None, "form": {}}
        elif request.mimetype == "application/x-www-form-urlencoded":
            envelope = {
                "json": None,
                "form": request.form.to_dict(flat=False),
                "text": None,
            }
        elif request.mimetype.startswith("multipart/"):
            raise BadRequest(
                "Use JSON, UTF-8 text or application/x-www-form-urlencoded"
            )
        else:
            envelope = {"json": None, "form": {}, "text": text}
        envelope.update(
            query=request.args.to_dict(flat=False), content_type=request.mimetype
        )
        return jsonify(
            reactive.receive(rid, envelope, request.headers.get("Idempotency-Key"))
        ), 202

    @routes.patch("/api/routines/<rid>/reactive")
    def reactive_config(rid):
        return jsonify(reactive.configure(rid, request.get_json()))

    @routes.get("/api/routines/<rid>/receipts")
    def routine_receipts(rid):
        return jsonify(
            reactive.receipts(
                rid,
                request.args.get("before", type=int),
                request.args.get("limit", 20, type=int),
            )
        )

    @routes.get("/api/routines/<rid>/receipts/<int:receipt_id>")
    def routine_receipt(rid, receipt_id):
        return jsonify(reactive.receipt(rid, receipt_id))

    @routes.delete("/api/routines/<rid>/receipts/<int:receipt_id>")
    def delete_routine_receipt(rid, receipt_id):
        return jsonify(reactive.delete_receipt(rid, receipt_id))

    @routes.post("/api/routines/<rid>/receipts/<int:receipt_id>/replay")
    def replay_routine_receipt(rid, receipt_id):
        return jsonify(reactive.replay(rid, receipt_id)), 202

    @routes.get("/api/routines/<rid>/normalizers")
    def routine_normalizers(rid):
        return jsonify(reactive.versions(rid))

    @routes.get("/api/routines/<rid>/normalizers/<vid>")
    def routine_normalizer(rid, vid):
        return jsonify(reactive.version(rid, vid))

    @routes.get("/api/routines/<rid>/sessions")
    def reactive_sessions(rid):
        return jsonify(reactive.sessions(rid, request.args.get("after")))

    @routes.post("/api/routines/<rid>/sessions/reset")
    def reset_reactive_session(rid):
        return jsonify(reactive.reset_session(rid, request.get_json()["session_key"]))

    @routes.post("/api/routines/<rid>/normalizers")
    def create_routine_normalizer(rid):
        values = request.get_json()
        return jsonify(
            reactive.create_version(
                rid, values.get("source"), values.get("dependencies")
            )
        ), 201

    @routes.post("/api/routines/<rid>/normalizers/<vid>/test")
    def test_routine_normalizer(rid, vid):
        return jsonify(reactive.test(rid, request.get_json()["receipt_id"], vid))
