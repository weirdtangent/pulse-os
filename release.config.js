// Note: @semantic-release/changelog and @semantic-release/git are not used
// because they require pushing commits to main, which is blocked by branch
// protection rules. Release notes are included in GitHub Releases instead.
module.exports = {
  branches: ["main"],
  plugins: [
    [
      "@semantic-release/commit-analyzer",
      {
        preset: "angular",
        releaseRules: [
          { type: "feat", release: "minor" },
          { type: "fix", release: "patch" },
          { type: "perf", release: "patch" },
          { type: "revert", release: "patch" },
          { type: "chore", release: "patch" },
          { type: "build", release: "patch" },
          { type: "ci", release: "patch" },
          { type: "docs", release: "patch" },
          { type: "refactor", release: "patch" },
          { type: "style", release: "patch" },
          { type: "test", release: "patch" },
          { breaking: true, release: "major" },
          { type: "*", release: "patch" }, // fallback for non-conventional commits
        ],
      },
    ],
    "@semantic-release/release-notes-generator",
    "@semantic-release/github",
  ],
};

