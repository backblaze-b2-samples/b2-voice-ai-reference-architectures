import { PutObjectCommand } from "@aws-sdk/client-s3";
import { describe, expect, it } from "vitest";
import {
  USER_AGENT,
  deleteVersion,
  endpointForRegion,
  listVersions,
  makeClient,
  presignGet,
  presignPut,
  readVersion,
  zoneBucket,
} from "../src/b2Client.js";
import { SETTINGS, newBucket, testClient } from "./helpers.js";

describe("client configuration", () => {
  it("derives the endpoint from the region", async () => {
    expect(endpointForRegion("us-west-004")).toBe("https://s3.us-west-004.backblazeb2.com");
    const endpoint = await makeClient(SETTINGS).config.endpoint?.();
    expect(`${endpoint?.protocol}//${endpoint?.hostname}`).toBe("https://s3.us-west-004.backblazeb2.com");
  });

  it("rejects a region that is not a B2 region name", () => {
    expect(() => endpointForRegion("https://evil.example.com/")).toThrow();
  });

  it("names the missing setting", () => {
    expect(() => makeClient({ B2_REGION: "us-west-004" })).toThrow(/B2_APPLICATION_KEY_ID/);
  });

  it("reads the zone bucket from its variable", () => {
    expect(zoneBucket("incoming", { B2_INCOMING_BUCKET: "voice-incoming" })).toBe("voice-incoming");
    expect(() => zoneBucket("releases", {})).toThrow(/B2_RELEASES_BUCKET/);
  });

  it("sends the user agent", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const seen: string[] = [];
    client.middlewareStack.add(
      (next) => async (args) => {
        const headers = (args.request as { headers: Record<string, string> }).headers;
        seen.push(`${headers["user-agent"] ?? ""} ${headers["x-amz-user-agent"] ?? ""}`);
        return next(args);
      },
      { step: "finalizeRequest", priority: "low", name: "capture-user-agent" },
    );
    await client.send(new PutObjectCommand({ Bucket: bucket, Key: "contributions/a.wav", Body: "x" }));
    expect(seen.length).toBeGreaterThan(0);
    for (const agent of seen) expect(agent).toContain(USER_AGENT);
  });
});

describe("presigned URLs and versions", () => {
  it("uploads with a presigned PUT and downloads with a presigned GET", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const putUrl = await presignPut(client, bucket, "contributions/clip.wav", { contentType: "audio/wav" });
    expect(new URL(putUrl).searchParams.get("X-Amz-SignedHeaders")).toContain("content-type");
    const put = await fetch(putUrl, { method: "PUT", body: "RIFF-data", headers: { "Content-Type": "audio/wav" } });
    expect(put.status).toBe(200);
    const get = await fetch(await presignGet(client, bucket, "contributions/clip.wav"));
    expect(await get.text()).toBe("RIFF-data");
  });

  it("lists, reads, presigns and deletes versions", async () => {
    const client = testClient();
    const bucket = await newBucket(client);
    const key = "datasets/v/manifest.json";
    await client.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: "one" }));
    await client.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: "two" }));
    const versions = await listVersions(client, bucket, "datasets/");
    expect(versions).toHaveLength(2);
    expect(versions.filter((v) => v.isLatest)).toHaveLength(1);
    const contents = await Promise.all(versions.map(async (v) => (await readVersion(client, bucket, key, v.versionId)).toString()));
    expect(contents.sort()).toEqual(["one", "two"]);
    const older = versions.find((v) => !v.isLatest)!;
    const url = await presignGet(client, bucket, key, { versionId: older.versionId });
    expect(await (await fetch(url)).text()).toBe((await readVersion(client, bucket, key, older.versionId)).toString());
    await deleteVersion(client, bucket, key, older.versionId);
    expect(await listVersions(client, bucket, "datasets/")).toHaveLength(1);
  });
});
