import { describe, expect, it } from "vitest";
import { EventProcessor, EventStore, SIGNATURE_HEADER, sign, verifySignature, type B2Event } from "../src/notifications.js";

const SECRET = "unit-test-signing-secret";

function eventBody(...ids: string[]): Buffer {
  const events = ids.map((eventId) => ({
    eventId,
    eventType: "b2:ObjectCreated:Upload",
    bucketName: "voice-incoming",
    objectName: `contributions/${eventId}.wav`,
  }));
  return Buffer.from(JSON.stringify({ events }));
}

function processor(handler: (event: B2Event) => void | Promise<void>) {
  return new EventProcessor(SECRET, new EventStore(), handler);
}

describe("signature", () => {
  it("uses the documented header name and v1 format", () => {
    expect(SIGNATURE_HEADER).toBe("X-Bz-Event-Notification-Signature");
    expect(sign(SECRET, "{}")).toMatch(/^v1=[0-9a-f]{64}$/);
  });

  it("accepts only the exact body, secret and version", () => {
    const body = eventBody("e1");
    const good = sign(SECRET, body);
    expect(verifySignature(SECRET, body, good)).toBe(true);
    expect(verifySignature(SECRET, Buffer.concat([body, Buffer.from(" ")]), good)).toBe(false);
    expect(verifySignature("another-secret", body, good)).toBe(false);
    expect(verifySignature(SECRET, body, undefined)).toBe(false);
    expect(verifySignature(SECRET, body, good.replace("v1=", "v2="))).toBe(false);
  });
});

describe("event processing", () => {
  it("rejects an invalid signature without processing", async () => {
    const handled: B2Event[] = [];
    const result = await processor((event) => void handled.push(event)).handleRequest(eventBody("e1"), `v1=${"0".repeat(64)}`);
    expect(result.status).toBe(401);
    expect(handled).toEqual([]);
  });

  it("processes a repeated event once", async () => {
    const handled: string[] = [];
    const p = processor((event) => void handled.push(event.eventId));
    const body = eventBody("e1");
    const header = sign(SECRET, body);
    expect(await p.handleRequest(body, header)).toEqual({ status: 200, body: { processed: 1, duplicates: 0 } });
    expect(await p.handleRequest(body, header)).toEqual({ status: 200, body: { processed: 0, duplicates: 1 } });
    expect(handled).toEqual(["e1"]);
  });

  it("deduplicates inside one delivery and across batches", async () => {
    const handled: string[] = [];
    const p = processor((event) => void handled.push(event.eventId));
    for (const body of [eventBody("e1", "e2", "e1"), eventBody("e2", "e3")]) {
      expect((await p.handleRequest(body, sign(SECRET, body))).status).toBe(200);
    }
    expect(handled).toEqual(["e1", "e2", "e3"]);
  });

  it("releases the event when the handler fails so redelivery is processed", async () => {
    const calls: string[] = [];
    const p = processor((event) => {
      calls.push(event.eventId);
      if (calls.length === 1) throw new Error("queue unavailable");
    });
    const body = eventBody("e1");
    const header = sign(SECRET, body);
    await expect(p.handleRequest(body, header)).rejects.toThrow("queue unavailable");
    expect(await p.handleRequest(body, header)).toEqual({ status: 200, body: { processed: 1, duplicates: 0 } });
    expect(calls).toEqual(["e1", "e1"]);
  });

  it("answers 400 for a signed body that is not an event notification", async () => {
    const body = Buffer.from("not json");
    expect((await processor(() => {}).handleRequest(body, sign(SECRET, body))).status).toBe(400);
  });
});
