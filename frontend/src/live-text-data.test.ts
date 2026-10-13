import test from "node:test";
import assert from "node:assert/strict";
import { LiveTextFeed, mergeLiveText } from "./live-text-data.ts";

function setup() {
  const frames: (() => void)[] = [];
  const feed = new LiveTextFeed(
    (fn) => {
      frames.push(() => fn(0));
      return frames.length;
    },
    () => {},
  );
  const flush = () => {
    for (const frame of frames.splice(0)) frame();
  };
  const append = (text: string, start: number, block = "b", job = "j") =>
    feed.append({
      job_id: job,
      type: "delta",
      payload: {
        block_id: block,
        text,
        text_offset: start,
        text_end: start + text.length,
      },
    });
  return { feed, append, flush };
}
test("live text arrives before persistence and reconciles without duplication, including emoji", () => {
  const { feed, append } = setup();
  append("Hi ", 0);
  append("🌍", 3);
  append("!", 5);
  assert.equal(mergeLiveText("", 0, feed.get("j:b")), "Hi 🌍!");
  assert.equal(mergeLiveText("Hi 🌍", 5, feed.get("j:b")), "Hi 🌍!");
  assert.equal(mergeLiveText("Hi 🌍!", 6, feed.get("j:b")), "Hi 🌍!");
  append("🌍", 3); // duplicate/out-of-order delivery
  assert.equal(mergeLiveText("", 0, feed.get("j:b")), "Hi 🌍!");
});
test("reconnect and joining a running routine wait for missing durable prefixes", () => {
  const { feed, append } = setup();
  append("world", 6);
  assert.equal(mergeLiveText("", 0, feed.get("j:b")), "");
  assert.equal(mergeLiveText("Hello ", 6, feed.get("j:b")), "Hello world");
  feed.clear();
  assert.equal(mergeLiveText("Hello ", 6, feed.get("j:b")), "Hello ");
  append("again", 12);
  assert.equal(mergeLiveText("Hello ", 6, feed.get("j:b")), "Hello ");
  assert.equal(
    mergeLiveText("Hello world ", 12, feed.get("j:b")),
    "Hello world again",
  );
});
test("fragments notify only their message and coalesce at animation frames", () => {
  const { feed, append, flush } = setup();
  let updates = 0,
    others = 0;
  feed.subscribe("j:b", () => updates++);
  feed.subscribe("other:b", () => others++);
  for (let i = 0; i < 100; i++) append("x", i);
  assert.equal(updates, 0);
  flush();
  assert.equal(updates, 1);
  assert.equal(others, 0);
  assert.equal(feed.get("j:b")?.text.length, 100);
});
test("terminal events discard speculative text and reject late duplicates", () => {
  const { feed, append } = setup();
  append("unfinished", 0);
  feed.settle([{ type: "interrupted", job_id: "j" }]);
  append("late", 10);
  assert.equal(feed.get("j:b"), undefined);
  assert.equal(mergeLiveText("saved", 5, feed.get("j:b")), "saved");
});
test("live tail memory is bounded even when the tab stops painting", () => {
  const { feed, append } = setup();
  append("x".repeat(100000), 0);
  assert.equal(feed.get("j:b")?.text.length, 65536);
  assert.equal(mergeLiveText("", 0, feed.get("j:b")), "");
  assert.equal(
    mergeLiveText("x".repeat(90000), 90000, feed.get("j:b")),
    "x".repeat(100000),
  );
  for (let i = 0; i < 200; i++) append("a", 0, String(i));
  assert.equal(feed.get("j:b"), undefined);
  assert.equal(feed.get("j:199")?.text, "a");
  feed.dispose();
  assert.equal(feed.get("j:199"), undefined);
});
