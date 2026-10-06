import handler from "vinext/server/fetch-handler";
import { handleScheduled, warmActivation, type WarmEnv, type WarmEvent } from "./warm";

const worker = {
  async fetch(request: Request, env: WarmEnv, ctx: Parameters<typeof handler.fetch>[2]): Promise<Response> {
    if (new URL(request.url).pathname === "/api/warm/activate") return warmActivation(request, env);
    return handler.fetch(request, env, ctx);
  },
  async scheduled(event: WarmEvent, env: WarmEnv): Promise<void> {
    await handleScheduled(event, env);
  },
};

export default worker;
