extends Node

                                       
 
                                                                                 
                                                                             
                                                                          
                                                                      

const GBNavWorldScript: Script = preload("res://nav/gb_nav_world.gd")
const GBNavModelScript: Script = preload("res://nav/gb_nav_model.gd")
const GBNavRunnerScript: Script = preload("res://nav/gb_nav_runner.gd")

const WARMUP_FRAMES: int = 30
const RECONCILE_FRAMES: int = 90


func _ready() -> void:
	if OS.get_environment("GB_NAV_DIAG") != "1":
		return
	process_mode = Node.PROCESS_MODE_ALWAYS
	call_deferred("_run")


func _run() -> void:
	for _i in range(WARMUP_FRAMES):
		await get_tree().physics_frame

	var player: Node = GBProbe.get_player()
	if not (player is CharacterBody2D):
		print("GB_NAV_DIAG {\"error\":\"no CharacterBody2D player\"}")
		get_tree().quit()
		return
	var body: CharacterBody2D = player as CharacterBody2D

	var runner = GBNavRunnerScript.new()
	var calibration: Dictionary = await runner.calibrate(self)
	if not bool(calibration.get("ok", false)):
		print("GB_NAV_DIAG " + JSON.stringify(calibration))
		get_tree().quit()
		return
	var physics: Dictionary = calibration["physics"]

	                                                              
	for _i in range(30):
		await get_tree().physics_frame

	var bottom: float = 100000.0
	var viewport: Viewport = get_viewport()
	if viewport != null:
		bottom = viewport.get_visible_rect().size.y * 4.0
	var world = GBNavWorldScript.capture(get_tree().current_scene, body, bottom)

	var solids: Array = world.solids
	var near: Array = []
	for r: Rect2 in solids:
		if absf(r.position.x - body.global_position.x) < 260.0:
			near.append("(%.0f,%.0f %.0fx%.0f)" % [r.position.x, r.position.y, r.size.x, r.size.y])

	print("GB_NAV_DIAG_WORLD " + JSON.stringify({
		"solids": solids.size(),
		"one_ways": (world.one_ways as Array).size(),
		"hazards": (world.hazards as Array).size(),
		"player_rect_local": {
			"x": world.player_rect_local.position.x, "y": world.player_rect_local.position.y,
			"w": world.player_rect_local.size.x, "h": world.player_rect_local.size.y,
		},
		"kill_y": world.kill_y,
		"player_pos": {"x": body.global_position.x, "y": body.global_position.y},
		"solids_near_player": near.slice(0, 8),
	}))

	                                                                      
	var model = GBNavModelScript.new()
	model.setup(world, physics, body.global_position, body.velocity, body.is_on_floor())

	var divergences: Array = []
	var first_bad: int = -1
	for frame: int in range(RECONCILE_FRAMES):
		                                                    
		var jump: bool = frame == 20
		if frame == 0:
			GBProbe.press("gb_right")
		if jump:
			GBProbe.press("gb_jump")
		await get_tree().physics_frame
		if jump:
			GBProbe.release("gb_jump")

		model.step(1, jump)
		var delta: Vector2 = model.pos - body.global_position
		if delta.length() > 4.0 and first_bad < 0:
			first_bad = frame
		if frame % 10 == 0 or frame == first_bad:
			divergences.append("f=%d model=(%.1f,%.1f) engine=(%.1f,%.1f) d=%.1f mfloor=%s efloor=%s" % [
				frame, model.pos.x, model.pos.y, body.global_position.x, body.global_position.y,
				delta.length(), str(model.on_floor), str(body.is_on_floor())
			])
	GBProbe.release("gb_right")

	print("GB_NAV_DIAG_RECONCILE " + JSON.stringify({
		"first_divergence_frame": first_bad,
		"model_dead": model.dead,
		"model_cause": model.cause,
		"samples": divergences,
	}))
	get_tree().quit()
