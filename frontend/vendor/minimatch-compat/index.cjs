"use strict";

// Older ESLint plugins call require("minimatch") directly. Keep that API while
// delegating every match and helper to the unmodified, patched upstream package.
const modern = require("minimatch-modern");
module.exports = Object.assign(modern.minimatch, modern);
