import { rateFormToInput, type RateFormState } from "./rateForm";
import type { RateProfile, RateProfileInput } from "./types";

interface SaveRateProfileOptions {
  editingRateId: string | null;
  form: RateFormState;
  createRateProfile: (input: RateProfileInput) => Promise<RateProfile>;
  updateRateProfile: (rateId: string, input: RateProfileInput) => Promise<RateProfile>;
  refreshRates: () => Promise<void>;
}

export async function saveRateProfile({
  editingRateId,
  form,
  createRateProfile,
  updateRateProfile,
  refreshRates
}: SaveRateProfileOptions): Promise<string> {
  const input = rateFormToInput(form);

  if (editingRateId) {
    await updateRateProfile(editingRateId, input);
    await refreshRates();
    return "Rate updated.";
  }

  await createRateProfile({ ...input, is_active: true });
  await refreshRates();
  return "Rate created.";
}
