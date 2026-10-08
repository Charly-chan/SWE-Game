class_name GBNavModel
extends RefCounted

                                                                                
                                                                     

const CONTACT_EPS: float = 0.25
const HAZARD_MARGIN: float = 1.0

var world: GBNavWorld
var physics: Dictionary = {}
var pos: Vector2 = Vector2.ZERO
var vel: Vector2 = Vector2.ZERO
var on_floor: bool = false
var time: float = 0.0
var dead: bool = false
var cause: String = ""


func setup(
	snapshot: GBNavWorld,
	measured_physics: Dictionary,
	position: Vector2,
	velocity: Vector2,
	floored: bool
) -> void:
	world = snapshot
	physics = measured_physics
	pos = position
	vel = velocity
	on_floor = floored
	time = snapshot.t0


func copy() -> GBNavModel:
	var model := GBNavModel.new()
	model.world = world
	model.physics = physics
	model.pos = pos
	model.vel = vel
	model.on_floor = on_floor
	model.time = time
	model.dead = dead
	model.cause = cause
	return model


func body_rect() -> Rect2:
	return world.body_rect(pos)


func key() -> String:
	                                                                         
	                                                                          
	return "%d,%d,%d,%d,%d,%d" % [
		int(round(pos.x / 6.0)),
		int(round(pos.y / 6.0)),
		int(round(vel.x / 30.0)),
		int(round(vel.y / 30.0)),
		1 if on_floor else 0,
		int(floor(time * 6.0)) % 60,
	]


func step(direction: int, jump: bool) -> void:
	if dead:
		return
	var ticks_per_second: float = float(physics["ticks_per_second"])
	var dt := 1.0 / ticks_per_second
	time += dt

	var run_speed: float = float(physics["run_speed"])
	var acceleration: float = float(
		physics["ground_acceleration"] if on_floor else physics["air_acceleration"]
	)
	var friction: float = float(
		physics["ground_friction"] if on_floor else physics["air_friction"]
	)
	if direction != 0:
		vel.x = move_toward(vel.x, float(direction) * run_speed, acceleration * dt)
	else:
		vel.x = move_toward(vel.x, 0.0, friction * dt)

	if jump and on_floor:
		vel.y = float(physics["jump_velocity"])
		on_floor = false
	elif not on_floor:
		vel.y += float(physics["gravity"]) * dt
		var terminal: float = float(physics["terminal_velocity"])
		if is_finite(terminal):
			vel.y = minf(vel.y, terminal)

	_move(dt)
	_contacts()


func _move(dt: float) -> void:
	var before: Rect2 = body_rect()
	var motion := vel * dt
	var obstacles: Array[Rect2] = world.solids.duplicate()
	obstacles.append_array(world.mover_rects(time))
	var p: Vector2 = before.position
	var remaining: Vector2 = motion
	on_floor = false

	                                                                            
	                                                                              
	for _pass: int in range(2):
		if remaining.length_squared() < 0.0000001:
			break
		var box := Rect2(p, before.size)
		var best_time := INF
		var best_axis := -1
		var best_bound := 0.0
		for obstacle: Rect2 in obstacles:
			var hit: Array = _sweep(box, remaining, obstacle)
			if hit.is_empty() or float(hit[0]) >= best_time:
				continue
			best_time = float(hit[0])
			best_axis = int(hit[1])
			best_bound = float(hit[2])
		if best_axis < 0:
			p += remaining
			remaining = Vector2.ZERO
			break
		var leftover: Vector2 = remaining * (1.0 - best_time)
		p += remaining * best_time
		p[best_axis] = best_bound
		if best_axis == 0:
			leftover.x = 0.0
			vel.x = 0.0
		else:
			if remaining.y > 0.0:
				on_floor = true
			leftover.y = 0.0
			vel.y = 0.0
		remaining = leftover
	p += remaining
	pos = p - world.player_rect_local.position

	if motion.y >= 0.0 and not on_floor:
		var after: Rect2 = body_rect()
		for platform: Rect2 in world.one_ways:
			if before.end.y > platform.position.y + CONTACT_EPS:
				continue
			if after.end.x <= platform.position.x or after.position.x >= platform.end.x:
				continue
			if after.end.y < platform.position.y or after.position.y >= platform.position.y:
				continue
			pos.y += platform.position.y - after.end.y
			vel.y = 0.0
			on_floor = true
			break

	                                                                         
	                                                              
	if not on_floor and motion.y >= 0.0:
		var snap: float = float(physics.get("floor_snap_length", 0.0))
		if snap > 0.0:
			_snap_to_floor(snap, obstacles)


func _snap_to_floor(distance: float, obstacles: Array[Rect2]) -> void:
	var box: Rect2 = body_rect()
	var best := INF
	for obstacle: Rect2 in obstacles:
		if box.end.x <= obstacle.position.x or box.position.x >= obstacle.end.x:
			continue
		var gap: float = obstacle.position.y - box.end.y
		if gap >= -CONTACT_EPS and gap <= distance:
			best = minf(best, obstacle.position.y)
	for platform: Rect2 in world.one_ways:
		if box.end.x <= platform.position.x or box.position.x >= platform.end.x:
			continue
		var gap: float = platform.position.y - box.end.y
		if gap >= -CONTACT_EPS and gap <= distance:
			best = minf(best, platform.position.y)
	if is_finite(best):
		pos.y += best - box.end.y
		vel.y = 0.0
		on_floor = true


func _contacts() -> void:
	if body_rect().position.y > world.kill_y:
		dead = true
		cause = "fall"
		return
	var danger_box: Rect2 = body_rect().grow(HAZARD_MARGIN)
	for hazard: Rect2 in world.hazard_rects(time):
		if danger_box.intersects(hazard, true):
			dead = true
			cause = "hazard"
			return


static func _sweep(box: Rect2, motion: Vector2, obstacle: Rect2) -> Array:
	var low: Vector2 = obstacle.position - box.size
	var high: Vector2 = obstacle.end
	var start: Vector2 = box.position
	var enter := Vector2(-INF, -INF)
	var leave := Vector2(INF, INF)
	for axis: int in range(2):
		if absf(motion[axis]) < 0.000001:
			if start[axis] <= low[axis] or start[axis] >= high[axis]:
				return []
		else:
			var first: float = (low[axis] - start[axis]) / motion[axis]
			var second: float = (high[axis] - start[axis]) / motion[axis]
			enter[axis] = minf(first, second)
			leave[axis] = maxf(first, second)
	var entry: float = maxf(enter.x, enter.y)
	var exit: float = minf(leave.x, leave.y)
	if entry >= exit or entry >= 1.0 or exit <= 0.0:
		return []
	var axis := 0 if enter.x > enter.y else 1
	if entry < 0.0:
		if -entry * absf(motion[axis]) > CONTACT_EPS:
			return []
		entry = 0.0
	return [entry, axis, low[axis] if motion[axis] > 0.0 else high[axis]]
