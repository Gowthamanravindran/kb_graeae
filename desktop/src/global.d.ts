export {};
declare global {
  interface Window {
    kb?: {
      baseUrl: string;
      token: string;
      openExternal(url: string): Promise<void>;
      openCitation(c: { project: string; branch: string; repo: string; file: string; line: number }): Promise<{ ok: boolean; error?: string }>;
      pickFile(filters?: { name: string; extensions: string[] }[]): Promise<string | null>;
      engineStatus(): Promise<{ ok: boolean; message?: string; log?: string; logPath?: string; stopped?: boolean }>;
      retryEngine(): Promise<void>;
      openLogs(): Promise<void>;
      onEngineStopped?(cb: () => void): () => void;
    };
  }
}
