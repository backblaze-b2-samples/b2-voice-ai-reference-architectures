// S3-compatible client for Backblaze B2, presigned URLs and object versions.
//
// Settings come from environment variables only:
//   B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY, B2_REGION
// The endpoint is derived from the region, so there is no endpoint setting to keep
// in sync. Every request carries the user agent below.
import { env } from "node:process";
import {
  DeleteObjectCommand,
  GetObjectCommand,
  ListObjectVersionsCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

export const USER_AGENT = "b2-voice-ai-reference-architectures";
const REGION_PATTERN = /^[a-z]{2}-[a-z]+-[0-9]{3}$/;

export type Settings = Record<string, string | undefined>;

export const ZONE_ENV = {
  incoming: "B2_INCOMING_BUCKET",
  work: "B2_WORK_BUCKET",
  releases: "B2_RELEASES_BUCKET",
  protected: "B2_PROTECTED_BUCKET",
} as const;
export type Zone = keyof typeof ZONE_ENV;

export function endpointForRegion(region: string): string {
  if (!REGION_PATTERN.test(region)) {
    throw new Error(`B2_REGION must look like 'us-west-004', got '${region}'`);
  }
  return `https://s3.${region}.backblazeb2.com`;
}

export function requireSetting(name: string, source: Settings = env): string {
  const value = source[name];
  if (!value) throw new Error(`Set ${name} in the environment`);
  return value;
}

export function zoneBucket(zone: Zone, source: Settings = env): string {
  if (!(zone in ZONE_ENV)) throw new Error(`Unknown zone '${zone}'`);
  return requireSetting(ZONE_ENV[zone], source);
}

// `endpoint` replaces the region-derived endpoint. It exists so tests can point the
// client at a local S3-compatible server; applications leave it unset.
export function makeClient(source: Settings = env, endpoint?: string): S3Client {
  const region = requireSetting("B2_REGION", source);
  return new S3Client({
    region,
    endpoint: endpoint ?? endpointForRegion(region),
    forcePathStyle: true,
    credentials: {
      accessKeyId: requireSetting("B2_APPLICATION_KEY_ID", source),
      secretAccessKey: requireSetting("B2_APPLICATION_KEY", source),
    },
    customUserAgent: USER_AGENT,
    maxAttempts: 3,
    // Send checksum headers only when an operation requires them.
    requestChecksumCalculation: "WHEN_REQUIRED",
    responseChecksumValidation: "WHEN_REQUIRED",
  });
}

// URL that downloads one object (or one version of it) without credentials.
export function presignGet(
  client: S3Client,
  bucket: string,
  key: string,
  options: { expiresIn?: number; versionId?: string } = {},
): Promise<string> {
  const command = new GetObjectCommand({ Bucket: bucket, Key: key, VersionId: options.versionId });
  return getSignedUrl(client, command, { expiresIn: options.expiresIn ?? 3600 });
}

// URL that uploads one object without credentials. When contentType is set it is part
// of the signature, so the uploader must send the same Content-Type header.
export function presignPut(
  client: S3Client,
  bucket: string,
  key: string,
  options: { contentType?: string; expiresIn?: number } = {},
): Promise<string> {
  const command = new PutObjectCommand({ Bucket: bucket, Key: key, ContentType: options.contentType });
  // The presigner leaves Content-Type unsigned unless it is listed as signable.
  const signableHeaders = options.contentType ? new Set(["content-type"]) : undefined;
  return getSignedUrl(client, command, { expiresIn: options.expiresIn ?? 900, signableHeaders });
}

export interface ObjectVersion {
  key: string;
  versionId: string;
  isLatest: boolean;
  size: number;
  hideMarker: boolean;
}

// Every version and hide marker under a prefix.
export async function listVersions(client: S3Client, bucket: string, prefix = ""): Promise<ObjectVersion[]> {
  const found: ObjectVersion[] = [];
  let keyMarker: string | undefined;
  let versionIdMarker: string | undefined;
  do {
    const page = await client.send(
      new ListObjectVersionsCommand({
        Bucket: bucket,
        Prefix: prefix,
        KeyMarker: keyMarker,
        VersionIdMarker: versionIdMarker,
      }),
    );
    for (const item of page.Versions ?? []) {
      found.push({ key: item.Key!, versionId: item.VersionId!, isLatest: !!item.IsLatest, size: item.Size ?? 0, hideMarker: false });
    }
    for (const item of page.DeleteMarkers ?? []) {
      found.push({ key: item.Key!, versionId: item.VersionId!, isLatest: !!item.IsLatest, size: 0, hideMarker: true });
    }
    keyMarker = page.IsTruncated ? page.NextKeyMarker : undefined;
    versionIdMarker = page.IsTruncated ? page.NextVersionIdMarker : undefined;
  } while (keyMarker);
  return found;
}

// Read the content of one specific version.
export async function readVersion(client: S3Client, bucket: string, key: string, versionId: string): Promise<Buffer> {
  const response = await client.send(new GetObjectCommand({ Bucket: bucket, Key: key, VersionId: versionId }));
  return Buffer.from(await response.Body!.transformToByteArray());
}

// Permanently delete one version. A plain delete only hides the key.
export async function deleteVersion(client: S3Client, bucket: string, key: string, versionId: string): Promise<void> {
  await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: key, VersionId: versionId }));
}
