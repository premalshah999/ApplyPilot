"""The applicant explicitly denies browser geolocation, including in popups."""

LOCATION_DENIED = """(() => {
  const denied = (_, failure) => { if (failure) queueMicrotask(() => failure({
    code:1, message:'Location sharing is disabled by the applicant', PERMISSION_DENIED:1
  })); return 0; };
  if(navigator.geolocation) {
    navigator.geolocation.getCurrentPosition=denied;
    navigator.geolocation.watchPosition=denied;
    navigator.geolocation.clearWatch=()=>{};
  }
})()"""


async def deny_location(context, page):
    session = await context.new_cdp_session(page)
    try:
        # Omitting origin denies this permission in the entire dedicated context.
        await session.send(
            "Browser.setPermission",
            {
                "permission": {"name": "geolocation"},
                "setting": "denied",
            },
        )
    finally:
        await session.detach()
    await page.add_init_script(LOCATION_DENIED)
    for frame in page.frames:
        try:
            await frame.evaluate(LOCATION_DENIED)
        except Exception:
            pass
