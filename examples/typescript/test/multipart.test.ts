import { GetObjectCommand, CreateMultipartUploadCommand } from "@aws-sdk/client-s3";
import { describe, expect, it } from "vitest";
import { MIB, Interrupted, bigFile, newBucket, testClient, watchUploadParts } from "./helpers.js";
import { UploadFailed, abortUpload, findUpload, storedParts, uploadFile } from "../src/multipart.js";

const read = async (client: ReturnType<typeof testClient>, bucket: string, key: string) =>
  Buffer.from(await (await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }))).Body!.transformToByteArray());

const unavailable = () => Object.assign(new Error("service unavailable"), { name: "ServiceUnavailable" });
const noSleep = async () => {};

describe("multipart upload", () => {
  it("uploads a file in parts", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const { path, content } = await bigFile();
    await uploadFile(client, bucket, "sessions/s1.wav", path);
    expect((await read(client, bucket, "sessions/s1.wav")).equals(content)).toBe(true);
  });

  it("resumes after an interruption and uploads only the missing parts", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const { path, content } = await bigFile();
    const key = "sessions/resume.wav";

    const first = watchUploadParts(client, "interrupt", (part) => (part === 2 ? new Interrupted() : undefined));
    await expect(uploadFile(client, bucket, key, path)).rejects.toBeInstanceOf(Interrupted);
    expect(first.uploaded).toEqual([1, 2]);
    first.stop();

    const uploadId = await findUpload(client, bucket, key);
    expect(uploadId).toBeDefined();
    expect([...(await storedParts(client, bucket, key, uploadId!)).keys()]).toEqual([1]);

    const second = watchUploadParts(client, "count");
    await uploadFile(client, bucket, key, path);
    expect(second.uploaded).toEqual([2, 3]);
    expect((await read(client, bucket, key)).equals(content)).toBe(true);
    expect(await findUpload(client, bucket, key)).toBeUndefined();
  });

  it("uploads again a stored part that does not match the file", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const { path, content } = await bigFile();
    const key = "sessions/changed.wav";
    const { UploadId } = await client.send(new CreateMultipartUploadCommand({ Bucket: bucket, Key: key }));
    const { UploadPartCommand } = await import("@aws-sdk/client-s3");
    await client.send(new UploadPartCommand({ Bucket: bucket, Key: key, UploadId, PartNumber: 1, Body: Buffer.alloc(5 * MIB, 122) }));
    const watch = watchUploadParts(client, "count");
    await uploadFile(client, bucket, key, path);
    expect(watch.uploaded).toEqual([1, 2, 3]);
    expect((await read(client, bucket, key)).equals(content)).toBe(true);
  });

  it("retries a transient failure", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const { path, content } = await bigFile();
    let failed = false;
    watchUploadParts(client, "flaky", () => {
      if (failed) return undefined;
      failed = true;
      return unavailable();
    });
    const delays: number[] = [];
    await uploadFile(client, bucket, "sessions/flaky.wav", path, { sleep: async (ms) => void delays.push(ms) });
    expect(delays).toHaveLength(1);
    expect((await read(client, bucket, "sessions/flaky.wav")).equals(content)).toBe(true);
  });

  it("keeps the upload for resume when retries run out", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const { path } = await bigFile();
    watchUploadParts(client, "down", unavailable);
    const error = await uploadFile(client, bucket, "sessions/down.wav", path, { attempts: 3, sleep: noSleep }).catch((e) => e);
    expect(error).toBeInstanceOf(UploadFailed);
    expect(error.uploadId).toBe(await findUpload(client, bucket, "sessions/down.wav"));
  });

  it("aborts on failure when asked, and aborts explicitly", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const { path } = await bigFile();
    const key = "sessions/abort.wav";
    const watch = watchUploadParts(client, "down", unavailable);
    const error = await uploadFile(client, bucket, key, path, { attempts: 2, abortOnFailure: true, sleep: noSleep }).catch((e) => e);
    expect(error).toBeInstanceOf(UploadFailed);
    expect(error.uploadId).toBeUndefined();
    expect(await findUpload(client, bucket, key)).toBeUndefined();
    watch.stop();

    const { UploadId } = await client.send(new CreateMultipartUploadCommand({ Bucket: bucket, Key: key }));
    await abortUpload(client, bucket, key, UploadId!);
    expect(await findUpload(client, bucket, key)).toBeUndefined();
  });

  it("rejects a part size below the minimum", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const { path } = await bigFile();
    await expect(uploadFile(client, bucket, "sessions/x.wav", path, { partSize: MIB })).rejects.toThrow(RangeError);
  });
});
