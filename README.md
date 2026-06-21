# 🦆 duckspec

A spec-driven development framework for building complex projects with AI.

## How it works

1. Define your project's vocabulary as YAML `@Term` files — each term carries properties, guidelines, and AI directives
2. Load them into context with `ducktools load` (CLI) or as an MCP server
3. The AI knows your domain, follows your rules, and executes `@Recipe` instructions on demand — no re-explaining every session

## Concepts

| Concept | What it is |
|---|---|
| `@Term` | A named element of your project vocabulary; one `.yaml` file per term |
| `extends` | Inheritance — a term inherits all properties and recipes from its parent |
| `@Recipe` | Named instructions the AI executes; called with `Invoke @TermName#recipe()` |
| `@DuckArch` | Built-in terms for describing software: `@Software`, `@Function`, `@Server`, `@Script`… |
| `DuckTools` | CLI and MCP server that loads terms into the AI's context |

## Example

```yaml
name: WeatherWidget
description: Minimal web app showing current weather at the user's location via IP geolocation.
extends: @Software
platform: Python
components:
  - name: server
    type: @Server
    src: server.py
    description: Python HTTP server; serves ui.html at GET / and exposes GET /weather
    functions:
      - name: get_weather
        description: >
          Gets the client IP from the request; calls http://ip-api.com/json/<ip> to resolve latitude, longitude, and city;
          calls https://api.open-meteo.com/v1/forecast?latitude=<lat>&longitude=<lon>&current=temperature_2m,weathercode;
          maps weathercode to a condition string
          (0=Clear, 1-3=Partly cloudy, 45-48=Fog, 51-67=Rain, 71-77=Snow, 80-82=Showers, 95-99=Thunderstorm);
          returns {"temperature": <°C float>, "condition": <string>, "city": <string>}
  - name: ui
    type: @UserInterface
    src: ui.html
    views:
      - name: main
        type: @View
        components:
          - name: refresh_button
            type: @Button
            label: Refresh
            signals:
              - name: clicked
                description: Emitted when the user clicks the button
          - name: weather_display
            type: @View
            description: Hidden until first data load
            components:
              - name: city_label
                type: @Label
                description: Displays the city name
              - name: temperature_label
                type: @Label
                description: Displays the temperature in °C
              - name: condition_label
                type: @Label
                description: Displays the weather condition string
    functions:
      - name: request_weather
        signal: refresh_button#clicked
        description: Calls server#get_weather via GET /weather; on success calls show_weather; on error displays "Failed to load weather"
      - name: show_weather
        description: Populates weather_display with city, temperature, and condition from the server response
```

`refresh_clicked` → `request_weather` → `GET /weather` → `server#get_weather` → ip-api + Open-Meteo → `show_weather`

## Goals

- Standardize how requirements and @Term definitions are written across projects
- Reduce the noise models introduce into the development process
- Provide tools for describing and navigating project structure
- Simplify repetitive development tasks

## Installation

Copy and send to your AI assistant:

> Install duckspec in this project: https://github.com/komorebinator/duckspec/blob/main/Duckspec.yaml
