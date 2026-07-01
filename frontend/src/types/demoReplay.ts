export type DemoReplayStatus = {
  mode: string;
  status: "ready" | "unconfigured" | "invalid";
  sampleCount: number;
  currentIndex: number;
  currentSampleId: string | null;
  loop: boolean;
};

export type DemoReplayLocation = {
  latitude: number;
  longitude: number;
};

export type DemoReplaySample = {
  sampleId: string;
  sequenceIndex: number;
  title: string;
  description: string;
  dashcamUrl: string;
  topviewUrl: string;
  location: DemoReplayLocation | null;
  headingDegrees: number | null;
  tags: string[];
};

export type DemoReplayCurrentResponse = {
  mode: string;
  sample: DemoReplaySample;
  sampleCount: number;
  currentIndex: number;
  hasNext: boolean;
};

export type DemoReplayAdvanceResponse = {
  previousSampleId: string;
  sample: DemoReplaySample;
  currentIndex: number;
  looped: boolean;
  sampleCount: number;
};

export type DemoReplayResetResponse = {
  sample: DemoReplaySample;
  currentIndex: number;
  sampleCount: number;
};
