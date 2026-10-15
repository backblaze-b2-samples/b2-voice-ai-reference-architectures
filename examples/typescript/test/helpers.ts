import { randomBytes } from "node:crypto";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { env } from "node:process";
import {
  CreateBucketCommand,
  PutBucketVersioningCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { makeClient } from "../src/b2Client.js";

export const MIB = 1024 * 1024;
export const SETTINGS = {
  B2_APPLICATION_KEY_ID: "test-key-id",
  B2_APPLICATION_KEY: "test-key-secret",
  B2_REGION: "us-west-004",
};

// tools/run_ts_examples.sh starts a local S3-compatible server and sets this variable.
export function serverUrl(): string {
  const url = env["MOTO_ENDPOINT"];
  if (!url) throw new Error("MOTO_ENDPOINT is not set; run these tests with tools/run_ts_examples.sh");
  return url;
}

export function testClient(): S3Client {
  return makeClient(SETTINGS, serverUrl());
}

export async function newBucket(client: S3Client, versioned = true): Promise<string> {
  const name = `test-${randomBytes(6).toString("hex")}`;
  await client.send(new CreateBucketCommand({ Bucket: name, CreateBucketConfiguration: { LocationConstraint: SETTINGS.B2_REGION as never } }));
  if (versioned) {
    await client.send(new PutBucketVersioningCommand({ Bucket: name, VersioningConfiguration: { Status: "Enabled" } }));
  }
  return name;
}

// 11 MiB: parts of 5, 5 and 1 MiB.
export async function bigFile(): Promise<{ path: string; content: Buffer }> {
  const dir = await mkdtemp(join(tmpdir(), "b2-examples-"));
  const path = join(dir, "session.wav");
  const content = randomBytes(11 * MIB);
  await writeFile(path, content);
  return { path, content };
}

export class Interrupted extends Error {}

// Record UploadPart calls (by part number); throw `failure(part)` for a part when it returns an error.
export function watchUploadParts(client: S3Client, name: string, failure?: (part: number) => Error | undefined) {
  const uploaded: number[] = [];
  client.middlewareStack.add(
    (next, context) => async (args) => {
      if (context.commandName === "UploadPartCommand") {
        const part = (args.input as { PartNumber: number }).PartNumber;
        uploaded.push(part);
        const error = failure?.(part);
        if (error) throw error;
      }
      return next(args);
    },
    { step: "initialize", name },
  );
  return { uploaded, stop: () => client.middlewareStack.remove(name) };
}
