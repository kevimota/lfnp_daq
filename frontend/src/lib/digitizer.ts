// Shared DT5742/DT5743 constants and pure conversions, used by both the
// New Scan and Edit Scan pages. Keep hardware calibration values here only.

// Valid scan configuration for each supported digitizer board model:
// DT5742 (18, DRS4) -> 5 / 2.5 / 1 / 0.75 GS/s, 16 channels (2 groups x 8)
// DT5743 (27, SAMLONG) -> 3.2 / 1.6 / 0.8 / 0.4 GS/s, 8 channels (4 groups x 2)
export const SAMPLING_FREQUENCIES: Record<number, number[]> = {
  18: [5_000_000_000, 2_500_000_000, 1_000_000_000, 750_000_000],
  27: [3_200_000_000, 1_600_000_000, 800_000_000, 400_000_000],
};
export const DEFAULT_SAMPLING: Record<number, number> = { 18: 5_000_000_000, 27: 3_200_000_000 };

export function channelCountForBoard(boardModel: number): number {
  if (boardModel === 18) return 16; // DT5742: 2 groups x 8 channels
  if (boardModel === 27) return 8; // DT5743: 4 groups x 2 channels
  return 8;
}

// DC-offset half-scale in mV per board: DT5742 (18) -> +/-0.5 V, DT5743 (27) -> +/-1.25 V.
// DAC code 0x8000 == 0 V (default center); code = 0x8000 + mv/half_scale * 0x8000.
const DC_OFFSET_HALF_SCALE_MV: Record<number, number> = { 18: 500, 27: 1250 };

export function dcOffsetHalfScaleMv(boardModel: number): number {
  return DC_OFFSET_HALF_SCALE_MV[boardModel] ?? 625;
}

export function dcOffsetMvToCode(mv: number, boardModel: number): number {
  const half = dcOffsetHalfScaleMv(boardModel);
  return Math.max(0, Math.min(0xffff, Math.round(0x8000 + (mv / half) * 0x8000)));
}

export function dcOffsetCodeToMv(code: number, boardModel: number): number {
  const half = dcOffsetHalfScaleMv(boardModel);
  return Math.round(((code - 0x8000) * half) / 0x8000);
}

// DT5742 TR0 (fast trigger) conversions. The mezzanine PCB is assumed to be
// rev >= 1: the TR0 input is attenuated by 2 before the DRS4/comparator.
// Threshold: 13.2 DAC counts per mV at the TR0 connector (DT5742 manual
// section 9.8.3, 0x6666 = 0 mV). DC offset: 0x8000 = 0 mV, +/-2000 mV at the
// connector because the offset DAC is after the /2 attenuator.
const TR0_THRESHOLD_MV_TO_CODE = 13.2;
const TR0_DC_OFFSET_HALF_SCALE_MV = 2000;

export function tr0ThresholdMvToCode(mv: number): number {
  return Math.max(0, Math.min(0xffff, Math.round(0x6666 + mv * TR0_THRESHOLD_MV_TO_CODE)));
}

export function tr0ThresholdCodeToMv(code: number): number {
  return Math.round((code - 0x6666) / TR0_THRESHOLD_MV_TO_CODE);
}

export function tr0DcOffsetMvToCode(mv: number): number {
  return Math.max(0, Math.min(0xffff, Math.round(0x8000 + (mv / TR0_DC_OFFSET_HALF_SCALE_MV) * 0x8000)));
}

export function tr0DcOffsetCodeToMv(code: number): number {
  return Math.round(((code - 0x8000) * TR0_DC_OFFSET_HALF_SCALE_MV) / 0x8000);
}

export function formatDacCode(code: number): string {
  return `0x${code.toString(16).toUpperCase()} (${code})`;
}

export type Tr0Preset = {
  id: string;
  label: string;
  offsetCode: number;
  thresholdCode: number;
  offsetMv: number;
  thresholdMv: number;
};

// DT5742 manual Table 9.1 (mezzanine PCB rev >= 1). Codes are the manual's
// exact values; the mV values are their connector-referred equivalents.
export const TR0_PRESETS: Tr0Preset[] = [
  { id: 'ecl', label: 'ECL', offsetCode: 0x55A0, thresholdCode: 0x6666, offsetMv: -662, thresholdMv: 0 },
  { id: 'nim', label: 'NIM (0 to -800 mV)', offsetCode: 0x8000, thresholdCode: 0x51C6, offsetMv: 0, thresholdMv: -400 },
  { id: 'neg400', label: 'Negative 0 to -400 mV', offsetCode: 0x8000, thresholdCode: 0x5C16, offsetMv: 0, thresholdMv: -200 },
  { id: 'neg200', label: 'Negative 0 to -200 mV', offsetCode: 0x8000, thresholdCode: 0x613E, offsetMv: 0, thresholdMv: -100 },
  { id: 'bipolar', label: 'Bipolar', offsetCode: 0x8000, thresholdCode: 0x6666, offsetMv: 0, thresholdMv: 0 },
  { id: 'ttl', label: 'TTL / Positive >= 2 V', offsetCode: 0xA800, thresholdCode: 0x6666, offsetMv: 625, thresholdMv: 0 },
  { id: 'pos2', label: 'Positive 0 to 2 V', offsetCode: 0x91A7, thresholdCode: 0x6666, offsetMv: 276, thresholdMv: 0 },
];

export function samplingOptions(boardModel: number): number[] {
  return SAMPLING_FREQUENCIES[boardModel] ?? [];
}

export function formatSamplingFrequency(hz: number): string {
  return `${hz / 1e9} GS/s`;
}

export function defaultSamplingHz(boardModel: number): number | null {
  return DEFAULT_SAMPLING[boardModel] ?? samplingOptions(boardModel)[0] ?? null;
}
