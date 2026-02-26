/**
 * Worker pool — caches WorkerClient instances by worker URL.
 *
 * Avoids re-creating HTTP clients per turn and provides a single
 * place to handle worker death (remove from pool).
 */

import { WorkerClient } from "./worker-client.js";

export class WorkerPool {
  private clients = new Map<string, WorkerClient>();

  get(workerUrl: string): WorkerClient {
    let client = this.clients.get(workerUrl);
    if (!client) {
      client = new WorkerClient(workerUrl);
      this.clients.set(workerUrl, client);
    }
    return client;
  }

  remove(workerUrl: string): void {
    this.clients.delete(workerUrl);
  }
}
