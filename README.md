# Auto.js SDK Plugins Garage

This repository stores third-party AutoJs6 plugin APKs and metadata.

Run the following command after changing plugin APKs, per-plugin `index.json`, icons, or localized resources:

```sh
python tools/generate_plugin_index.py
```

The command updates `plugins.generated.json`, a prebuilt index consumed by AutoJs6 Plugin Center.
