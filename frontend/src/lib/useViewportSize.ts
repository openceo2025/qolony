import { useEffect, useState } from "react";

export function useViewportSize(minWidth = 360, minHeight = 320): { width: number; height: number } {
  const calc = () => ({
    width: Math.max(minWidth, window.innerWidth - 64),
    height: Math.max(minHeight, window.innerHeight - 280),
  });

  const [size, setSize] = useState(calc);

  useEffect(() => {
    const onResize = () => setSize(calc());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [minWidth, minHeight]);

  return size;
}
