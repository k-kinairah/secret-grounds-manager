import type { NextRequest } from "next/server";
import { proxyCancel } from "@/lib/assistantServer";

export async function POST(request: NextRequest) {
  return proxyCancel(request);
}
