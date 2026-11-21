package cortex.policy

# METADATA
# title: Policy bundle revision
# description: |
#   The revision of the bundle that owns data.cortex.policy: a content hash of
#   every policy file, set by `opa build --revision` (infra/opa/build-bundle.sh).
#   Decisions return it next to their result, so a context pack records the
#   exact policies it was authorised under (docs/discovery.md 7.3). Two bundles
#   owning cortex/policy would be a deployment error, and OPA reports it as a
#   conflict instead of picking one.
# entrypoint: true
revision := rev if {
	some bundle in data.system.bundles
	"cortex/policy" in bundle.manifest.roots
	rev := bundle.manifest.revision
}
