# Minimatch compatibility adapter

The legacy CommonJS consumers in ESLint 9 and its React/import/accessibility
plugins expect a callable `require("minimatch")`. Modern consumers use named
exports. Both interfaces delegate to the official, unmodified minimatch 11.0.3
package; this adapter contains no pattern parser or copied legacy implementation.

The root override selects this private adapter for every minimatch consumer. Its
ES module entry re-exports the official modern API, and its CommonJS entry also
preserves the legacy callable API. The actual matching implementation is the
unmodified npm alias `minimatch-modern@npm:minimatch@11.0.3`, which remains visible
to dependency auditing. The local prerelease suffix identifies the adapter;
it is not a republished upstream distribution. Keep the raw npm audit gate enabled.

This addresses [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)
without changing the configured lint rules. Remove the adapter when those plugins
support the modern export shape. Run `npm run test:security` and `npm run lint`
after updating the pinned upstream implementation.
