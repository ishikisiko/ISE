# Weather Conditions

Use `weather_conditions` for current weather, forecasts up to three days, and
current air quality when the user explicitly supplies a location.

Examples: `What is the weather in Bend, Oregon today?`, `北京明天天气`,
`新加坡空气质量如何`.

Do not invent a location from account, device, or IP context. A request such as
`Will it rain tomorrow?` must be rejected with `location_required` unless the
conversation supplies an explicit inherited location.
