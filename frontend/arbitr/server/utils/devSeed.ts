// The dev user selector's data (issue 28): the seed's people and scopes, read
// from data/synthetic/tenant.json mounted into the dev container. People are
// role labels only (no names); ids are the seed's UUIDs. Dev-only: with no
// NUXT_DEV_SEED_FILE the route answers 404.

export interface DevPerson { key: string, label: string, role: string, id: string, tenantId: string }
export interface DevScope { key: string, name: string, id: string, type: string, tenantId: string }
export interface DevSeed { people: DevPerson[], scopes: DevScope[] }

interface SeedNode { key: string, obj_id: string, name: string, type: string, path_up: string[] }
interface SeedPerson { key: string, label: string, role: string, node: string, obj_id: string }

export function devSeed(doc: { nodes: SeedNode[], people: SeedPerson[] }): DevSeed {
  const byKey = new Map(doc.nodes.map(n => [n.key, n]))
  const tenantOf = (key: string): string => {
    const tenant = (byKey.get(key)?.path_up ?? []).map(k => byKey.get(k)).find(n => n?.type === 'tenant')
    return tenant?.obj_id ?? ''
  }
  return {
    people: doc.people.map(p => ({ key: p.key, label: p.label, role: p.role, id: p.obj_id, tenantId: tenantOf(p.node) })),
    scopes: doc.nodes
      .filter(n => n.type !== 'organization')
      .map(n => ({ key: n.key, name: n.name, id: n.obj_id, type: n.type, tenantId: tenantOf(n.key) }))
  }
}
