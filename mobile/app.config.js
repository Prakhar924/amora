/**
 * Dynamic Expo config.
 *
 * Everything lives in `app.json`; this wrapper only injects the settings that
 * depend on *where* the build is being hosted.
 *
 * `experiments.baseUrl` is required when the web build is served from a
 * subdirectory, as it is on GitHub Pages (`/amora/`). It is applied only when
 * `EXPO_BASE_URL` is set, so local development and native builds — which are
 * always served from the root — are untouched.
 */

const base = require('./app.json');

module.exports = ({ config }) => {
  const baseUrl = process.env.EXPO_BASE_URL;

  return {
    ...config,
    ...base.expo,
    experiments: {
      ...(base.expo.experiments ?? {}),
      ...(baseUrl ? { baseUrl } : {}),
    },
  };
};
