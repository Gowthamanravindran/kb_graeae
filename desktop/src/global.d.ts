export {};
declare global {
  interface Window {
    kb?: {
      baseUrl: string;
      token: string;
      openExternal(url: string): Promise<void>;
      openFile(path: string, line?: number): Promise<void>;
      pickFile(filters?: { name: string; extensions: string[] }[]): Promise<string | null>;
      engineStatus(): Promise<{ ok: boolean; message?: string; log?: string; logPath?: string }>;
      retryEngine(): Promise<void>;
      openLogs(): Promise<void>;
    };
  }
}
