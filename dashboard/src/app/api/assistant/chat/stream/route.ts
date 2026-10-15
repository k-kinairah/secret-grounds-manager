import type { NextRequest } from "next/server";
import { proxyChatStream } from "@/lib/assistantServer";

export async function POST(request: NextRequest) {
  return proxyChatStream(request);
}
