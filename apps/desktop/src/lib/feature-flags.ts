export interface FeatureFlagsProvider { isEnabled(name: string, fallback: boolean): boolean }
export class LocalFeatureFlagsProvider implements FeatureFlagsProvider {
  constructor(private values: Readonly<Record<string, boolean>> = {}) {}
  isEnabled(name: string, fallback: boolean) { return this.values[name] ?? fallback; }
}
export class FeatureFlagService {
  constructor(private provider: FeatureFlagsProvider = new LocalFeatureFlagsProvider()) {}
  isEnabled(name: string, fallback = false): boolean {
    try { return this.provider.isEnabled(name, fallback); } catch { return fallback; }
  }
}
// No remote fetch and no business features enabled. Future kill switch implementations
// may change the provider, never the local-memory truth or signature verification policy.
export const featureFlags = new FeatureFlagService();
