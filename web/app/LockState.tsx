"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

type S = { unlocked: boolean; gate: boolean };

export default function LockState() {
  const router = useRouter();
  const [s, setS] = useState<S | null>(null);
  useEffect(() => {
    fetch("/auth/status", { cache: "no-store" }).then((r) => r.json()).then(setS).catch(() => setS(null));
  }, []);
  if (!s?.gate) return null;
  const lock = async () => {
    await fetch("/auth/lock", { method: "POST" }).catch(() => null);
    router.push("/unlock");
  };
  return (
    <div className="lock">
      {s.unlocked
        ? <><span>Unlocked</span><button onClick={lock}>Lock</button></>
        : <><span>Locked</span><Link href="/unlock">Unlock</Link></>}
    </div>
  );
}
