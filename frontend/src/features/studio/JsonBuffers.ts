import { createContext, useRef, useState } from "react";

type Buffer = { text: string; original: string; error?: unknown };
export function useJsonBuffers() {
  const [buffers, setBuffers] = useState<Record<string, Buffer>>({});
  const generation = useRef(0);
  function update(key: string, value?: Buffer) {
    generation.current++;
    setBuffers((old) => {
      const next = { ...old };
      if (value) next[key] = value;
      else delete next[key];
      return next;
    });
  }
  return { buffers, update, generation };
}
export const JsonBuffers = createContext<
  (ReturnType<typeof useJsonBuffers> & { draftId: string }) | null
>(null);
