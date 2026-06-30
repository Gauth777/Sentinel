export type MediaTelemetrySource = "demo" | "live" | "unavailable";

export type MediaLocation = {
  latitude: number;
  longitude: number;
};

export type MediaTelemetry = {
  location?: MediaLocation | null;
  headingDegrees?: number | null;
  speedKmh?: number | null;
  capturedAt: string;
  telemetrySource: MediaTelemetrySource;
};

export type MediaUploadResponse = {
  mediaId: string;
  uri: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
  storageMode: "managed_upload";
  telemetry?: MediaTelemetry | null;
  createdAt: string;
};

export type CapturedMedia = {
  uri: string;
  width: number;
  height: number;
  mimeType: string;
  capturedAt: string;
};

export type CaptureState =
  | "checking_permission"
  | "permission_denied"
  | "camera_ready"
  | "capturing"
  | "preview"
  | "uploading"
  | "upload_success"
  | "upload_error";
