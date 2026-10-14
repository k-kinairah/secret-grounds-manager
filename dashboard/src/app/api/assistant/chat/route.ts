import type { NextRequest } from "next/server";
import { proxyChat } from "@/lib/assistantServer";

export async function POST(request: NextRequest) {
  return proxyChat(request);
}
