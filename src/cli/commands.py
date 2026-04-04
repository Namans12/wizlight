"""Click-based CLI for WizLight."""

import asyncio

import click

from ..core.async_runtime import BackgroundAsyncLoop, configure_event_loop_policy
from ..core.bulb_controller import BulbController, apply_preset, PRESETS
from ..core.config import Config


def run_async(ctx, coro):
    """Run async work on the command-scoped event loop."""
    return ctx.obj["runner"].run(coro)


def _cleanup_runtime(controller: BulbController, runner: BackgroundAsyncLoop) -> None:
    """Close bulb transports before stopping the CLI event loop."""
    try:
        runner.run(controller.close_async(), timeout=2.0)
    except Exception:
        controller.close()
    finally:
        runner.shutdown()


def get_bulb_ips(config: Config, ip: str = None) -> list[str]:
    """Get list of bulb IPs - either specified or all configured."""
    if ip:
        return [ip]
    return [b.ip for b in config.bulbs]


@click.group()
@click.pass_context
def cli(ctx):
    """WizLight - Control your WiZ smart bulbs."""
    configure_event_loop_policy()
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()
    ctx.obj["controller"] = BulbController()
    ctx.obj["runner"] = BackgroundAsyncLoop()
    ctx.call_on_close(lambda: _cleanup_runtime(ctx.obj["controller"], ctx.obj["runner"]))


@cli.command()
@click.option("--broadcast", "-b", default="192.168.1.255", help="Broadcast address for discovery")
@click.option("--save/--no-save", default=True, help="Save discovered bulbs to config")
@click.pass_context
def discover(ctx, broadcast, save):
    """Discover WiZ bulbs on the local network."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    
    click.echo(f"Discovering bulbs on {broadcast}...")
    bulbs = run_async(ctx, controller.discover(broadcast))
    
    if not bulbs:
        click.echo("No bulbs found. Check your network and broadcast address.")
        return
    
    click.echo(f"Found {len(bulbs)} bulb(s):")
    for i, bulb in enumerate(bulbs, 1):
        click.echo(f"  {i}. IP: {bulb['ip']}, MAC: {bulb['mac']}")
        if save:
            config.add_bulb(bulb['ip'], f"Bulb {i}", bulb['mac'])
    
    if save:
        click.echo(f"Saved to config: {config._config_path}")


@cli.command("add-bulb")
@click.argument("ip")
@click.option("--name", "-n", default=None, help="Name for the bulb")
@click.pass_context
def add_bulb(ctx, ip, name):
    """Manually add a bulb by IP address."""
    config = ctx.obj["config"]
    controller = ctx.obj["controller"]
    
    # Test connection
    click.echo(f"Testing connection to {ip}...")
    try:
        state = run_async(ctx, controller.get_state(ip))
        status_str = "ON" if state.is_on else "OFF"
        click.echo(f"  [ok] Bulb responding. Status: {status_str}")
        
        bulb_name = name or f"Bulb {len(config.bulbs) + 1}"
        config.add_bulb(ip, bulb_name)
        click.echo(f"  [ok] Added '{bulb_name}' ({ip}) to config")
    except Exception as e:
        click.echo(f"  [error] Could not connect to bulb: {e}")
        click.echo("    Make sure the bulb is on and connected to WiFi")


@cli.command("remove-bulb")
@click.argument("ip")
@click.pass_context
def remove_bulb(ctx, ip):
    """Remove a bulb from config."""
    config = ctx.obj["config"]
    
    if config.remove_bulb(ip):
        click.echo(f"Removed bulb {ip} from config")
    else:
        click.echo(f"Bulb {ip} not found in config")


@cli.command("list-bulbs")
@click.pass_context
def list_bulbs(ctx):
    """List all configured bulbs."""
    config = ctx.obj["config"]
    
    if not config.bulbs:
        click.echo("No bulbs configured.")
        return
    
    click.echo("Configured bulbs:")
    for bulb in config.bulbs:
        click.echo(f"  - {bulb.name} - {bulb.ip}")


@cli.command()
@click.pass_context
def status(ctx):
    """Show status of all configured bulbs."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    
    if not config.bulbs:
        click.echo("No bulbs configured. Run 'wizlight discover' first.")
        return

    async def get_states():
        return await asyncio.gather(
            *(controller.get_state(bulb.ip) for bulb in config.bulbs),
            return_exceptions=True,
        )

    for bulb_config, result in zip(config.bulbs, run_async(ctx, get_states())):
        if isinstance(result, Exception):
            click.echo(f"{bulb_config.name} ({bulb_config.ip}): ERROR - {result}")
            continue

        status_str = "ON" if result.is_on else "OFF"
        extras = []
        if result.brightness is not None:
            extras.append(f"brightness={result.brightness}")
        if result.rgb is not None:
            extras.append(f"rgb={result.rgb}")
        if result.color_temp is not None:
            extras.append(f"temp={result.color_temp}K")

        extra_str = f" ({', '.join(extras)})" if extras else ""
        click.echo(f"{bulb_config.name} ({bulb_config.ip}): {status_str}{extra_str}")


@cli.command()
@click.option("--ip", "-i", help="Specific bulb IP (default: all bulbs)")
@click.option("--brightness", "-b", type=int, help="Brightness level (0-255)")
@click.pass_context
def on(ctx, ip, brightness):
    """Turn on bulb(s)."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = get_bulb_ips(config, ip)
    
    if not ips:
        click.echo("No bulbs configured.")
        return
    
    run_async(ctx, controller.turn_on_all(ips, brightness))
    click.echo(f"Turned on {len(ips)} bulb(s)")


@cli.command()
@click.option("--ip", "-i", help="Specific bulb IP (default: all bulbs)")
@click.pass_context
def off(ctx, ip):
    """Turn off bulb(s)."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = get_bulb_ips(config, ip)
    
    if not ips:
        click.echo("No bulbs configured.")
        return
    
    run_async(ctx, controller.turn_off_all(ips))
    click.echo(f"Turned off {len(ips)} bulb(s)")


@cli.command()
@click.option("--ip", "-i", help="Specific bulb IP (default: all bulbs)")
@click.pass_context
def toggle(ctx, ip):
    """Toggle bulb(s) on/off."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = get_bulb_ips(config, ip)
    
    if not ips:
        click.echo("No bulbs configured.")
        return
    
    run_async(ctx, controller.toggle_all(ips))
    click.echo(f"Toggled {len(ips)} bulb(s)")


@cli.command()
@click.argument("level", type=int)
@click.option("--ip", "-i", help="Specific bulb IP (default: all bulbs)")
@click.pass_context
def brightness(ctx, level, ip):
    """Set brightness (0-255)."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = get_bulb_ips(config, ip)
    
    if not ips:
        click.echo("No bulbs configured.")
        return

    run_async(ctx, controller.turn_on_all(ips, level))
    click.echo(f"Set brightness to {level} on {len(ips)} bulb(s)")


@cli.command()
@click.argument("r", type=int)
@click.argument("g", type=int)
@click.argument("b", type=int)
@click.option("--ip", "-i", help="Specific bulb IP (default: all bulbs)")
@click.option("--brightness", "-B", type=int, help="Brightness level (0-255)")
@click.pass_context
def color(ctx, r, g, b, ip, brightness):
    """Set RGB color (0-255 for each)."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = get_bulb_ips(config, ip)
    
    if not ips:
        click.echo("No bulbs configured.")
        return
    
    run_async(ctx, controller.set_rgb_all(ips, r, g, b, brightness))
    click.echo(f"Set color to RGB({r}, {g}, {b}) on {len(ips)} bulb(s)")


@cli.command()
@click.argument("kelvin", type=int)
@click.option("--ip", "-i", help="Specific bulb IP (default: all bulbs)")
@click.option("--brightness", "-b", type=int, help="Brightness level (0-255)")
@click.pass_context
def temp(ctx, kelvin, ip, brightness):
    """Set color temperature in Kelvin (2200-6500)."""
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = get_bulb_ips(config, ip)
    
    if not ips:
        click.echo("No bulbs configured.")
        return
    
    run_async(ctx, controller.set_color_temp_all(ips, kelvin, brightness))
    click.echo(f"Set color temperature to {kelvin}K on {len(ips)} bulb(s)")


@cli.command()
@click.argument("name", required=False)
@click.option("--ip", "-i", help="Specific bulb IP (default: all bulbs)")
@click.pass_context
def preset(ctx, name, ip):
    """Apply a color preset. Run without arguments to list presets."""
    if not name:
        click.echo("Available presets:")
        for preset_name, settings in PRESETS.items():
            click.echo(f"  {preset_name}: {settings}")
        return
    
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = get_bulb_ips(config, ip)
    
    if not ips:
        click.echo("No bulbs configured.")
        return
    
    success = run_async(ctx, apply_preset(controller, ips, name))
    if success:
        click.echo(f"Applied preset '{name}' to {len(ips)} bulb(s)")
    else:
        click.echo(f"Unknown preset '{name}'. Run 'wizlight preset' to see available presets.")


@cli.command()
@click.pass_context
def config_path(ctx):
    """Show configuration file path."""
    config = ctx.obj["config"]
    click.echo(config._config_path)


@cli.command()
@click.option("--port", "-p", type=int, default=38901, help="WebSocket server port")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.pass_context
def serve(ctx, port, host):
    """Start WebSocket server for Chrome extension and Android app.
    
    This allows external clients to sync colors with your WiZ bulbs.
    The Chrome extension connects to ws://localhost:38901 by default.
    """
    from ..features.websocket_server import ColorSyncServer
    
    controller = ctx.obj["controller"]
    config = ctx.obj["config"]
    ips = [b.ip for b in config.bulbs]
    
    if not ips:
        click.echo("No bulbs configured. Run 'wizlight discover' first.")
        return
    
    click.echo(f"Starting WebSocket server on ws://{host}:{port}")
    click.echo(f"Controlling {len(ips)} bulb(s): {', '.join(ips)}")
    click.echo("Press Ctrl+C to stop")
    click.echo()
    
    def on_color_change(colors: dict) -> None:
        """Handle color changes from WebSocket clients."""
        from ..features.screen_sync import build_bulb_color_map
        
        bulb_colors = build_bulb_color_map(
            ips,
            colors,
            config.screen_sync.mode,
            config.screen_sync.bulb_layout,
        )
        
        # Apply colors to bulbs
        async def apply_colors():
            await asyncio.gather(
                *(controller.set_rgb(ip, *rgb) for ip, rgb in bulb_colors.items()),
                return_exceptions=True,
            )
        
        ctx.obj["runner"].run(apply_colors())
    
    async def run_server():
        server = ColorSyncServer(on_color_change, port=port, host=host)
        await server.start()
        
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await server.stop()
    
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        click.echo("\nServer stopped")


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def alexa(ctx, verbose):
    """Start Alexa voice control bridge.
    
    This creates virtual WeMo devices that Alexa can discover and control.
    Say "Alexa, discover devices" to find them, then use voice commands like
    "Alexa, turn on Party Mode" or "Alexa, turn off WizLight".
    
    Requires fauxmo: pip install fauxmo
    """
    import subprocess
    import sys
    
    from ..features.alexa_bridge import (
        create_default_bridge,
        start_action_server,
    )
    
    config = ctx.obj["config"]
    controller = ctx.obj["controller"]
    ips = [b.ip for b in config.bulbs]
    
    if not ips:
        click.echo("No bulbs configured. Run 'wizlight discover' first.")
        return
    
    # Create and configure the bridge
    bridge = create_default_bridge(controller, ips)
    config_path = bridge.save_config()
    
    click.echo("WizLight Alexa Bridge")
    click.echo("=" * 40)
    click.echo(f"Controlling {len(ips)} bulb(s)")
    click.echo()
    click.echo("Virtual devices for Alexa:")
    for device in bridge.devices:
        click.echo(f"  • {device.name} (port {device.port})")
    click.echo()
    
    # Start HTTP callback server
    click.echo("Starting callback server on port 38900...")
    start_action_server(bridge, port=38900)
    
    # Start fauxmo
    click.echo("Starting fauxmo WeMo emulator...")
    click.echo()
    click.echo('Say "Alexa, discover devices" to find your WizLight.')
    click.echo("Press Ctrl+C to stop")
    click.echo()
    
    fauxmo_args = ["fauxmo", "-c", str(config_path)]
    if verbose:
        fauxmo_args.append("-v")
    
    try:
        # Check if fauxmo is installed
        subprocess.run(["fauxmo", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        click.echo("Error: fauxmo not found. Install it with:")
        click.echo("  pip install fauxmo")
        return
    
    try:
        # Run fauxmo (blocks until Ctrl+C)
        subprocess.run(fauxmo_args)
    except KeyboardInterrupt:
        click.echo("\nAlexa bridge stopped")


if __name__ == "__main__":
    cli()
