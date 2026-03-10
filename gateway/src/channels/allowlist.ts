/**
 * Allow-list for channel message filtering.
 * Case-insensitive matching with wildcard ('*') support for dev/testing.
 */
export class AllowList {
  private ids: Set<string>;

  constructor(ids: string[] = []) {
    this.ids = new Set(ids.map((id) => id.toLowerCase()));
  }

  isAllowed(senderId: string): boolean {
    if (this.ids.has("*")) return true;
    return this.ids.has(senderId.toLowerCase());
  }

  add(senderId: string): void {
    this.ids.add(senderId.toLowerCase());
  }

  remove(senderId: string): void {
    this.ids.delete(senderId.toLowerCase());
  }

  toArray(): string[] {
    return Array.from(this.ids);
  }
}
