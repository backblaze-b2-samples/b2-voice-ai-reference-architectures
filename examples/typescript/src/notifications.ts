// Verify B2 Event Notifications and process each event once.
//
// B2 signs the raw request body with HMAC SHA-256 and sends the result in the
// X-Bz-Event-Notification-Signature header as `v1=<hex digest>`. Delivery is at least
// once, so the same eventId can arrive more than once; EventProcessor claims each id
// before running the handler, so a repeated event is acknowledged without running the
// handler again. If the handler throws, the claim is released so B2's retry can process
// the event.
//
// The signing secret comes from the environment (B2_EVENT_SIGNING_SECRET); it is never
// stored with the rule or in source control.
import { createHmac, timingSafeEqual } from "node:crypto";

export const SIGNATURE_HEADER = "X-Bz-Event-Notification-Signature";
const SIGNATURE_VERSION = "v1";

// Header value for a body: `v1=` followed by the hex HMAC SHA-256.
export function sign(secret: string, rawBody: Buffer | string): string {
  return `${SIGNATURE_VERSION}=${createHmac("sha256", secret).update(rawBody).digest("hex")}`;
}

// Constant-time check of the signature header against the raw body.
export function verifySignature(secret: string, rawBody: Buffer | string, headerValue: string | null | undefined): boolean {
  if (!headerValue) return false;
  const expected = Buffer.from(sign(secret, rawBody));
  const received = Buffer.from(headerValue.trim());
  return expected.length === received.length && timingSafeEqual(expected, received);
}

export interface B2Event {
  eventId: string;
  eventType?: string;
  bucketName?: string;
  objectName?: string;
  [field: string]: unknown;
}

// Claimed event ids. This one lives in memory; use a shared database when several
// workers run or when the process can restart between deliveries.
export class EventStore {
  private readonly claimed = new Set<string>();
  claim(eventId: string): boolean {
    if (this.claimed.has(eventId)) return false;
    this.claimed.add(eventId);
    return true;
  }
  release(eventId: string): void {
    this.claimed.delete(eventId);
  }
}

export interface RequestResult {
  status: number;
  body: Record<string, unknown>;
}

export class EventProcessor {
  constructor(
    private readonly secret: string,
    private readonly store: EventStore,
    private readonly handler: (event: B2Event) => void | Promise<void>,
  ) {}

  // Status and summary for one webhook request. Answer within three seconds: the
  // handler should only enqueue work.
  async handleRequest(rawBody: Buffer | string, signatureHeader: string | null | undefined): Promise<RequestResult> {
    if (!verifySignature(this.secret, rawBody, signatureHeader)) {
      return { status: 401, body: { error: "invalid signature" } };
    }
    let events: B2Event[];
    try {
      events = JSON.parse(rawBody.toString()).events;
      if (!Array.isArray(events)) throw new TypeError("events");
    } catch {
      return { status: 400, body: { error: "body is not an event notification" } };
    }
    let processed = 0;
    let duplicates = 0;
    for (const event of events) {
      if (!this.store.claim(event.eventId)) {
        duplicates++;
        continue;
      }
      try {
        await this.handler(event);
      } catch (error) {
        this.store.release(event.eventId);
        throw error;
      }
      processed++;
    }
    return { status: 200, body: { processed, duplicates } };
  }
}
