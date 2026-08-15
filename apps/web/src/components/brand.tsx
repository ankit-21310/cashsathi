import Link from "next/link";

import { publicProductName } from "@/lib/env";

export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link className="brand" href={href} aria-label={`${publicProductName()} home`}>
      <span className="brand-mark" aria-hidden="true">₹</span>
      <span>{publicProductName()}</span>
    </Link>
  );
}
