# Brand icon

Home Assistant does **not** serve integration icons from a custom component's
own directory — putting `icon.png` in `custom_components/upright_go2/` does
nothing, and the UI shows an "icon not available" placeholder. The only
supported route is the [home-assistant/brands](https://github.com/home-assistant/brands)
repository, which has a `custom_integrations/` tree for exactly this case.

The files here are already in the required format:

```
custom_integrations/upright_go2/icon.png      256x256
custom_integrations/upright_go2/icon@2x.png   512x512
```

To get the icon showing in Home Assistant, copy this `custom_integrations/`
directory into a fork of home-assistant/brands and open a pull request. Once it
is merged, `https://brands.home-assistant.io/upright_go2/icon.png` resolves and
Home Assistant picks it up with no change to this integration.

Entity icons are unaffected — those come from `icons.json` in the integration
and work locally today.
