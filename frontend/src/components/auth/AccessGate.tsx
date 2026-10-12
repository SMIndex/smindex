// The access code screen was removed for the public launch (owner's decision).
// The site is open to everyone. Kept as a pass-through so App.tsx is unchanged.
export default function AccessGate({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
