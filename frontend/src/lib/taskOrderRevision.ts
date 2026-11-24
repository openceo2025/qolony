const FNV_OFFSET_BASIS_64 = 0xcbf29ce484222325n;
const FNV_PRIME_64 = 0x100000001b3n;
const UINT64_MASK = 0xffffffffffffffffn;

export function taskOrderRevision(orderedTaskIds: string[]): string {
  const encoded = new TextEncoder().encode(JSON.stringify(orderedTaskIds));
  let hashValue = FNV_OFFSET_BASIS_64;
  for (const byte of encoded) {
    hashValue ^= BigInt(byte);
    hashValue = (hashValue * FNV_PRIME_64) & UINT64_MASK;
  }
  return `order-v1-${hashValue.toString(16).padStart(16, "0")}`;
}
