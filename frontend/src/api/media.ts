/** Media API client for Sentinel managed upload pipeline.
 *
 * Reuses the existing backend base URL strategy.
 */
import { ApiError } from "@/src/api/sentinel";
import type { MediaUploadResponse, MediaTelemetry } from "@/src/types/media";

function backendBase() {
  return (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

function parseUploadError(status: number, detail?: string): string {
  if (status === 413) return "Image exceeds the upload limit.";
  if (status === 415) return "Unsupported or invalid image format.";
  if (status === 400) return detail || "Invalid image metadata.";
  return `Upload failed (${status})`;
}

export const mediaApi = {
  hasBackend: () => Boolean(backendBase()),

  uploadMedia: async (input: {
    fileUri: string;
    mimeType: string;
    fileName: string;
    telemetry?: MediaTelemetry | null;
  }) => {
    const base = backendBase();
    if (!base) {
      throw new ApiError("EXPO_PUBLIC_BACKEND_URL is not configured");
    }

    const formData = new FormData();

    // Append file — React Native uses a Blob-like object for file URIs.
    // The cast below is required because React Native's FormData accepts
    // objects with { uri, type, name } that TypeScript's DOM FormData does
    // not recognise.
    const fileEntry = {
      uri: input.fileUri,
      type: input.mimeType,
      name: input.fileName,
    } as unknown as Blob;
    formData.append("file", fileEntry);

    if (input.telemetry?.location) {
      formData.append("latitude", String(input.telemetry.location.latitude));
      formData.append("longitude", String(input.telemetry.location.longitude));
    }
    if (typeof input.telemetry?.headingDegrees === "number") {
      formData.append("headingDegrees", String(input.telemetry.headingDegrees));
    }
    if (typeof input.telemetry?.speedKmh === "number") {
      formData.append("speedKmh", String(input.telemetry.speedKmh));
    }
    if (input.telemetry?.capturedAt) {
      formData.append("capturedAt", input.telemetry.capturedAt);
    }
    if (input.telemetry?.telemetrySource) {
      formData.append("telemetrySource", input.telemetry.telemetrySource);
    }

    let res: Response;
    try {
      res = await fetch(`${base}/api/sentinel/media`, {
        method: "POST",
        body: formData,
      });
    } catch (err: unknown) {
      throw new ApiError(`Network error: ${getErrorMessage(err)}`);
    }

    if (!res.ok) {
      let detail: string | undefined;
      try {
        const body = await res.json();
        detail = body?.detail;
      } catch {
        // ignore parse failure
      }
      const msg = parseUploadError(res.status, detail);
      throw new ApiError(msg, res.status);
    }
    return (await res.json()) as MediaUploadResponse;
  },

  getMedia: async (mediaId: string): Promise<MediaUploadResponse> => {
    const base = backendBase();
    if (!base) {
      throw new ApiError("EXPO_PUBLIC_BACKEND_URL is not configured");
    }
    let res: Response;
    try {
      res = await fetch(`${base}/api/sentinel/media/${encodeURIComponent(mediaId)}`);
    } catch (err: unknown) {
      throw new ApiError(`Network error: ${getErrorMessage(err)}`);
    }
    if (!res.ok) {
      throw new ApiError(`Media fetch responded ${res.status}`, res.status);
    }
    return (await res.json()) as MediaUploadResponse;
  },

  getMediaFileUrl: (mediaId: string) => {
    const base = backendBase();
    if (!base) return null;
    return `${base}/api/sentinel/media/${encodeURIComponent(mediaId)}/file`;
  },
};