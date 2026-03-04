import { useEffect, useState } from 'react';
import { onDataChange, getConnection, getConversations, getWorkPlans, getWorkItems, type Conversation, type WorkPlan, type WorkItem } from '@/lib/spacetimedb-client';

/**
 * useSpacetimeDB React hook.
 * 
 * Subscribes to the SpacetimeDB client data changes and triggers
 * a re-render in the component whenever table data is updated.
 * 
 * @param selector A function that selects data from the database.
 * @param deps Dependencies for the selector.
 */
export function useSpacetimeDB<T>(selector: (db: any) => T, deps: any[] = []): T {
  const [data, setData] = useState<T>(() => selector(getConnection()));

  useEffect(() => {
    const unsubscribe = onDataChange(() => {
      setData(selector(getConnection()));
    });
    
    // Initial fetch in case connection was established before mount
    setData(selector(getConnection()));
    
    return unsubscribe;
  }, [...deps]);

  return data;
}

/**
 * Hook to get conversations from SpacetimeDB.
 */
export function useConversations(): Conversation[] {
  return useSpacetimeDB(() => getConversations());
}

/**
 * Hook to get work plans from SpacetimeDB.
 */
export function useWorkPlans(): WorkPlan[] {
  return useSpacetimeDB(() => getWorkPlans());
}

/**
 * Hook to get work items for a specific plan from SpacetimeDB.
 */
export function useWorkItems(planId: string): WorkItem[] {
  return useSpacetimeDB(() => getWorkItems(planId), [planId]);
}

/**
 * Hook to get SpacetimeDB connection status.
 */
export function useSpacetimeConnection() {
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const check = () => {
      const conn = getConnection();
      setConnected(!!conn && conn.isActive);
    };

    check();
    return onDataChange(check);
  }, []);

  return { connected };
}
