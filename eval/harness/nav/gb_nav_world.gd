class_name GBNavWorld
extends RefCounted

                                                                         
                                                                      

var solids: Array[Rect2] = []
var one_ways: Array[Rect2] = []
var hazards: Array[Rect2] = []
var movers: Array[Dictionary] = []
var player_rect_local: Rect2 = Rect2()
var kill_y: float = 100000.0
var t0: float = 0.0


static func capture(
	root: Node,
	player: CharacterBody2D,
	world_bottom: float,
	mover_velocities: Dictionary = {}
) -> GBNavWorld:
	var world := GBNavWorld.new()
	world.kill_y = world_bottom
	world.t0 = float(Engine.get_physics_frames()) / float(Engine.physics_ticks_per_second)
	world.player_rect_local = _player_local_rect(player)
	_collect_world(root, player, world, mover_velocities)
	world.solids = _merge_rects(world.solids)
	world.one_ways = _merge_rects(world.one_ways)
	return world


static func collision_rects(node: Node) -> Array[Rect2]:
	var rects: Array[Rect2] = []
	_collect_collision_rects(node, rects, false, null)
	return rects


func body_rect(position: Vector2) -> Rect2:
	return Rect2(position + player_rect_local.position, player_rect_local.size)


func mover_rects(at_time: float) -> Array[Rect2]:
	var result: Array[Rect2] = []
	for mover: Dictionary in movers:
		var delta: Vector2 = (at_time - t0) * (mover.get("velocity", Vector2.ZERO) as Vector2)
		for base_rect: Rect2 in mover.get("rects", []):
			result.append(Rect2(base_rect.position + delta, base_rect.size))
	return result


func hazard_rects(at_time: float) -> Array[Rect2]:
	var result: Array[Rect2] = hazards.duplicate()
	for mover: Dictionary in movers:
		if not bool(mover.get("hazard", false)):
			continue
		var delta: Vector2 = (at_time - t0) * (mover.get("velocity", Vector2.ZERO) as Vector2)
		for base_rect: Rect2 in mover.get("rects", []):
			result.append(Rect2(base_rect.position + delta, base_rect.size))
	return result


static func _collect_world(
	node: Node,
	player: CharacterBody2D,
	world: GBNavWorld,
	mover_velocities: Dictionary
) -> void:
	if node == player:
		return
	if node is TileMap:
		_collect_tile_map(node as TileMap, world)
		return
	if node is TileMapLayer:
		_collect_tile_map_layer(node as TileMapLayer, world)
		return
	if node is StaticBody2D:
		_collect_collision_rects(node, world.solids, true, world.one_ways)
		return
	if node is AnimatableBody2D or node is RigidBody2D or node is CharacterBody2D:
		var rects: Array[Rect2] = []
		_collect_collision_rects(node, rects, false, null)
		if not rects.is_empty():
			var velocity: Vector2 = Vector2.ZERO
			if mover_velocities.has(node.get_instance_id()):
				velocity = mover_velocities[node.get_instance_id()]
			elif node is CharacterBody2D:
				velocity = (node as CharacterBody2D).velocity
			elif node is RigidBody2D:
				velocity = (node as RigidBody2D).linear_velocity
			world.movers.append({
				"node": weakref(node),
				"rects": rects,
				"velocity": velocity,
				"hazard": node.is_in_group(&"gb_hazard"),
			})
		return
	if node.is_in_group(&"gb_hazard"):
		var hazard_rects: Array[Rect2] = []
		_collect_collision_rects(node, hazard_rects, false, null)
		world.hazards.append_array(hazard_rects)
		return
	for child: Node in node.get_children():
		_collect_world(child, player, world, mover_velocities)


static func _player_local_rect(player: CharacterBody2D) -> Rect2:
	var rects: Array[Rect2] = []
	_collect_collision_rects(player, rects, false, null)
	if rects.is_empty():
		return Rect2()
	var merged: Rect2 = rects[0]
	for index: int in range(1, rects.size()):
		merged = merged.merge(rects[index])
	return Rect2(merged.position - player.global_position, merged.size)


static func _collect_collision_rects(
	node: Node,
	result: Array[Rect2],
	separate_one_way: bool,
	one_way_result: Variant
) -> void:
	if node is CollisionShape2D:
		var collision := node as CollisionShape2D
		if not collision.disabled and collision.shape != null:
			var rect: Rect2 = _transformed_rect(collision.shape.get_rect(), collision.global_transform)
			if separate_one_way and collision.one_way_collision and one_way_result is Array:
				(one_way_result as Array).append(rect)
			else:
				result.append(rect)
	elif node is CollisionPolygon2D:
		var polygon := node as CollisionPolygon2D
		if not polygon.disabled and not polygon.polygon.is_empty():
			var rect: Rect2 = _points_rect(polygon.polygon, polygon.global_transform)
			if separate_one_way and polygon.one_way_collision and one_way_result is Array:
				(one_way_result as Array).append(rect)
			else:
				result.append(rect)
	for child: Node in node.get_children():
		_collect_collision_rects(child, result, separate_one_way, one_way_result)


static func _collect_tile_map(tile_map: TileMap, world: GBNavWorld) -> void:
	if tile_map.tile_set == null:
		return
	for map_layer: int in range(tile_map.get_layers_count()):
		for cell: Vector2i in tile_map.get_used_cells(map_layer):
			var tile_data: TileData = tile_map.get_cell_tile_data(map_layer, cell)
			if tile_data == null:
				continue
			var cell_transform := tile_map.global_transform * Transform2D(0.0, tile_map.map_to_local(cell))
			_collect_tile_data(tile_map.tile_set, tile_data, cell_transform, world)


static func _collect_tile_map_layer(layer: TileMapLayer, world: GBNavWorld) -> void:
	if layer.tile_set == null:
		return
	for cell: Vector2i in layer.get_used_cells():
		var tile_data: TileData = layer.get_cell_tile_data(cell)
		if tile_data == null:
			continue
		var cell_transform := layer.global_transform * Transform2D(0.0, layer.map_to_local(cell))
		_collect_tile_data(layer.tile_set, tile_data, cell_transform, world)


static func _collect_tile_data(
	tile_set: TileSet,
	tile_data: TileData,
	cell_transform: Transform2D,
	world: GBNavWorld
) -> void:
	for physics_layer: int in range(tile_set.get_physics_layers_count()):
		for polygon_index: int in range(tile_data.get_collision_polygons_count(physics_layer)):
			var points: PackedVector2Array = tile_data.get_collision_polygon_points(
				physics_layer, polygon_index
			)
			if points.is_empty():
				continue
			var rect: Rect2 = _points_rect(points, cell_transform)
			if tile_data.is_collision_polygon_one_way(physics_layer, polygon_index):
				world.one_ways.append(rect)
			else:
				world.solids.append(rect)


static func _transformed_rect(rect: Rect2, transform: Transform2D) -> Rect2:
	var points := PackedVector2Array([
		rect.position,
		Vector2(rect.end.x, rect.position.y),
		rect.end,
		Vector2(rect.position.x, rect.end.y),
	])
	return _points_rect(points, transform)


static func _points_rect(points: PackedVector2Array, transform: Transform2D) -> Rect2:
	var first: Vector2 = transform * points[0]
	var result := Rect2(first, Vector2.ZERO)
	for index: int in range(1, points.size()):
		result = result.expand(transform * points[index])
	return result


static func _merge_rects(source: Array[Rect2]) -> Array[Rect2]:
	                                                                             
	                                                                        
	var pending: Array[Rect2] = source.duplicate()
	var changed := true
	while changed:
		changed = false
		for i: int in range(pending.size()):
			for j: int in range(i + 1, pending.size()):
				var a: Rect2 = pending[i]
				var b: Rect2 = pending[j]
				var same_row := is_equal_approx(a.position.y, b.position.y) \
					and is_equal_approx(a.size.y, b.size.y)
				var same_column := is_equal_approx(a.position.x, b.position.x) \
					and is_equal_approx(a.size.x, b.size.x)
				if same_row and (is_equal_approx(a.end.x, b.position.x) or is_equal_approx(b.end.x, a.position.x)):
					pending[i] = a.merge(b)
					pending.remove_at(j)
					changed = true
					break
				if same_column and (is_equal_approx(a.end.y, b.position.y) or is_equal_approx(b.end.y, a.position.y)):
					pending[i] = a.merge(b)
					pending.remove_at(j)
					changed = true
					break
			if changed:
				break
	return pending
