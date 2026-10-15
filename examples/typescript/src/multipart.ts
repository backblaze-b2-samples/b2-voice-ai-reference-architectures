// Multipart upload with retry, resume, complete and abort.
//
// Parts are uploaded one at a time and every call is retried with exponential backoff
// on transient failures. When a run stops (process killed, network lost, retries
// exhausted) the unfinished upload stays in B2; the next call with the same key finds
// it, lists the parts already stored, checks each against the local file and uploads
// only the rest. abortUpload discards an upload explicitly; the lifecycle templates in
// this repository also cancel unfinished uploads.
import { createHash } from "node:crypto";
import { open, stat } from "node:fs/promises";
import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  ListMultipartUploadsCommand,
  ListPartsCommand,
  S3Client,
  UploadPartCommand,
  type CompleteMultipartUploadCommandOutput,
  type CompletedPart,
  type Part,
} from "@aws-sdk/client-s3";

export const MIN_PART_SIZE = 5 * 1024 * 1024; // smallest size for every part but the last
export const MAX_PARTS = 10_000;
const TRANSIENT_NAMES = new Set(["InternalError", "ServiceUnavailable", "SlowDown", "RequestTimeout", "Throttling", "ThrottlingException", "TimeoutError"]);
const TRANSIENT_CODES = new Set(["ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "EPIPE", "ENOTFOUND", "UND_ERR_SOCKET"]);

export class UploadFailed extends Error {
  constructor(message: string, readonly uploadId: string | undefined) {
    super(message);
    this.name = "UploadFailed";
  }
}

export function isTransient(error: unknown): boolean {
  const e = error as { name?: string; code?: string; $metadata?: { httpStatusCode?: number } };
  const status = e?.$metadata?.httpStatusCode ?? 0;
  return status >= 500 || TRANSIENT_NAMES.has(e?.name ?? "") || TRANSIENT_CODES.has(e?.code ?? "");
}

export type Sleep = (milliseconds: number) => Promise<void>;
const realSleep: Sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Run `call`, retrying transient errors with exponential backoff and jitter.
export async function withRetry<T>(call: () => Promise<T>, attempts = 5, baseDelayMs = 500, sleep: Sleep = realSleep): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await call();
    } catch (error) {
      if (!isTransient(error) || attempt >= attempts) throw error;
      await sleep(baseDelayMs * 2 ** (attempt - 1) * (0.5 + Math.random()));
    }
  }
}

// The id of the newest unfinished upload for exactly this key, if any.
export async function findUpload(client: S3Client, bucket: string, key: string): Promise<string | undefined> {
  let newest: { id: string; initiated: number } | undefined;
  let keyMarker: string | undefined;
  let uploadIdMarker: string | undefined;
  do {
    const page = await client.send(
      new ListMultipartUploadsCommand({ Bucket: bucket, Prefix: key, KeyMarker: keyMarker, UploadIdMarker: uploadIdMarker }),
    );
    for (const upload of page.Uploads ?? []) {
      if (upload.Key !== key) continue;
      const initiated = upload.Initiated?.getTime() ?? 0;
      if (!newest || initiated >= newest.initiated) newest = { id: upload.UploadId!, initiated };
    }
    keyMarker = page.IsTruncated ? page.NextKeyMarker : undefined;
    uploadIdMarker = page.IsTruncated ? page.NextUploadIdMarker : undefined;
  } while (keyMarker);
  return newest?.id;
}

// Parts already stored for an upload, by part number.
export async function storedParts(client: S3Client, bucket: string, key: string, uploadId: string): Promise<Map<number, Part>> {
  const parts = new Map<number, Part>();
  let marker: string | undefined;
  do {
    const page = await client.send(new ListPartsCommand({ Bucket: bucket, Key: key, UploadId: uploadId, PartNumberMarker: marker }));
    for (const part of page.Parts ?? []) parts.set(part.PartNumber!, part);
    marker = page.IsTruncated ? String(page.NextPartNumberMarker) : undefined;
  } while (marker);
  return parts;
}

// Discard an unfinished upload and the parts stored for it.
export async function abortUpload(client: S3Client, bucket: string, key: string, uploadId: string): Promise<void> {
  await client.send(new AbortMultipartUploadCommand({ Bucket: bucket, Key: key, UploadId: uploadId }));
}

export interface UploadOptions {
  partSize?: number;
  resume?: boolean; // continue an unfinished upload for the same key (default true)
  abortOnFailure?: boolean; // abort when a part still fails after all retries
  attempts?: number;
  sleep?: Sleep;
}

export async function uploadFile(
  client: S3Client,
  bucket: string,
  key: string,
  path: string,
  options: UploadOptions = {},
): Promise<CompleteMultipartUploadCommandOutput> {
  const { partSize = MIN_PART_SIZE, resume = true, abortOnFailure = false, attempts = 5, sleep = realSleep } = options;
  if (partSize < MIN_PART_SIZE) throw new RangeError(`partSize must be at least ${MIN_PART_SIZE} bytes`);
  const { size } = await stat(path);
  const totalParts = Math.max(1, Math.ceil(size / partSize));
  if (totalParts > MAX_PARTS) throw new RangeError(`${totalParts} parts exceeds the limit of ${MAX_PARTS}; raise partSize`);

  const existing = resume ? await findUpload(client, bucket, key) : undefined;
  let done = new Map<number, Part>();
  let id: string;
  if (existing === undefined) {
    const created = await withRetry(() => client.send(new CreateMultipartUploadCommand({ Bucket: bucket, Key: key })), attempts, 500, sleep);
    id = created.UploadId!;
  } else {
    id = existing;
    done = await withRetry(() => storedParts(client, bucket, key, existing), attempts, 500, sleep);
  }

  const completed: CompletedPart[] = [];
  const handle = await open(path, "r");
  try {
    for (let number = 1; number <= totalParts; number++) {
      const buffer = Buffer.alloc(Math.min(partSize, size - (number - 1) * partSize));
      await handle.read(buffer, 0, buffer.length, (number - 1) * partSize);
      const etag = `"${createHash("md5").update(buffer).digest("hex")}"`; // part checksum, not a security use
      const stored = done.get(number);
      if (stored && stored.Size === buffer.length && stored.ETag === etag) {
        completed.push({ PartNumber: number, ETag: stored.ETag });
        continue;
      }
      const response = await withRetry(
        () => client.send(new UploadPartCommand({ Bucket: bucket, Key: key, UploadId: id, PartNumber: number, Body: buffer })),
        attempts, 500, sleep,
      );
      completed.push({ PartNumber: number, ETag: response.ETag });
    }
    return await withRetry(
      () => client.send(new CompleteMultipartUploadCommand({ Bucket: bucket, Key: key, UploadId: id, MultipartUpload: { Parts: completed } })),
      attempts, 500, sleep,
    );
  } catch (error) {
    if (abortOnFailure) {
      await abortUpload(client, bucket, key, id);
      throw new UploadFailed(`upload of ${key} aborted: ${(error as Error).message}`, undefined);
    }
    if (isTransient(error)) throw new UploadFailed(`upload of ${key} stopped; call uploadFile again to resume`, id);
    throw error;
  } finally {
    await handle.close();
  }
}
