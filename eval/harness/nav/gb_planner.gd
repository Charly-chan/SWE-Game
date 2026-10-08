class_name GBPlanner
extends RefCounted

                                                                               
                                                                              

const MAX_EXPAND: int = 4000
const MAX_MS: int = 5000
const MAX_TICKS: int = 1400
const AIR_CAP: int = 180

var expansions: int = 0
var closed_size: int = 0
var frontier_exhausted: bool = false
var search_pruned: bool = false
var nearest_gap: float = INF
var nearest_point: Vector2 = Vector2.ZERO
var plan_ms: int = 0

var _goal_rect: Rect2
var _states: Array[GBNavModel] = []
var _parents: Array[int] = []
var _edge_inputs: Array[Array] = []
var _edge_actions: Array[Dictionary] = []
var _costs: Array[float] = []
var _heap: Array[Array] = []


func plan(start: GBNavModel, goal_rect: Rect2) -> Dictionary:
	var started: int = Time.get_ticks_usec()
	_goal_rect = goal_rect
	expansions = 0
	closed_size = 0
	frontier_exhausted = false
	search_pruned = false
	nearest_gap = INF
	nearest_point = start.pos
	_states = [start]
	_parents = [-1]
	_edge_inputs = [[]]
	_edge_actions = [{}]
	_costs = [0.0]
	_heap = []
	var seen: Dictionary = {start.key(): true}
	_push(_heuristic(start), 0)
	var found := -1
	var wall_clock_hit := false

	while not _heap.is_empty() and expansions < MAX_EXPAND:
		if (expansions & 63) == 0 and Time.get_ticks_usec() - started > MAX_MS * 1000:
			wall_clock_hit = true
			search_pruned = true
			break
		var current_index: int = _pop()
		var current: GBNavModel = _states[current_index]
		var gap: float = _rect_gap(current.body_rect(), _goal_rect)
		if gap < nearest_gap:
			nearest_gap = gap
			nearest_point = current.pos
		if _reached(current):
			found = current_index
			break
		expansions += 1
		if _costs[current_index] > MAX_TICKS:
			search_pruned = true
			continue
		for action: Dictionary in _moves(current):
			var edge: Dictionary = _simulate(current, action)
			if edge.is_empty():
				continue
			var next: GBNavModel = edge["model"]
			var reached: bool = bool(edge.get("reached", false))
			var key: String = next.key()
			if seen.has(key) and not reached:
				continue
			seen[key] = true
			var inputs: Array = edge["inputs"]
			var cost: float = _costs[current_index] + float(inputs.size())
			_states.append(next)
			_parents.append(current_index)
			_edge_inputs.append(inputs)
			_edge_actions.append(action)
			_costs.append(cost)
			var next_index: int = _states.size() - 1
			if reached:
				found = next_index
				break
			_push(cost + _heuristic(next), next_index)
		if found >= 0:
			break

	if expansions >= MAX_EXPAND:
		search_pruned = true
	frontier_exhausted = _heap.is_empty() and found < 0 and not search_pruned
	closed_size = seen.size()
	plan_ms = int((Time.get_ticks_usec() - started) / 1000)
	if found < 0:
		return {
			"ok": false,
			"inputs": [],
			"trajectory": [],
			"actions": [],
			"expansions": expansions,
			"closed": closed_size,
			"budget_hit": expansions >= MAX_EXPAND or wall_clock_hit,
			"time_budget_hit": wall_clock_hit,
			"frontier_exhausted": frontier_exhausted,
			"nearest_gap": nearest_gap,
			"nearest_point": nearest_point,
			"plan_ms": plan_ms,
		}
	return _rebuild(found)


func _moves(model: GBNavModel) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	if model.on_floor:
		for direction: int in [-1, 1]:
			result.append({"op": "run", "direction": direction, "ticks": 8})
			result.append({"op": "run", "direction": direction, "ticks": 20})
			result.append({"op": "fall", "direction": direction})
			result.append({"op": "jump", "direction": direction})
		result.append({"op": "jump", "direction": 0})
		result.append({"op": "wait", "direction": 0, "ticks": 8})
		result.append({"op": "wait", "direction": 0, "ticks": 24})
	else:
		for direction: int in [-1, 0, 1]:
			result.append({"op": "air", "direction": direction, "ticks": 8})
	return result


func _simulate(start: GBNavModel, action: Dictionary) -> Dictionary:
	var model: GBNavModel = start.copy()
	var inputs: Array = []
	var operation: String = String(action["op"])
	var direction: int = int(action.get("direction", 0))
	var ticks: int = int(action.get("ticks", 8))
	var left_floor := false
	var touched_goal := false
	var index := 0
	while true:
		var jump: bool = operation == "jump" and index == 0
		model.step(direction, jump)
		inputs.append([direction, 1 if jump else 0])
		if model.dead:
			return {}
		index += 1
		if not model.on_floor:
			left_floor = true
		if _reached(model):
			touched_goal = true
		if operation in ["run", "wait", "air"]:
			if touched_goal and operation in ["run", "wait"]:
				break
			if index >= ticks:
				break
		else:
			if left_floor and model.on_floor and index >= 3:
				break
			if index >= AIR_CAP:
				search_pruned = true
				break
			if operation == "fall" and not left_floor and index >= 40:
				break
	return {"model": model, "inputs": inputs, "reached": touched_goal}


func _rebuild(found: int) -> Dictionary:
	var chain: Array[int] = []
	var index := found
	while index > 0:
		chain.push_front(index)
		index = _parents[index]
	var inputs: Array = []
	var actions: Array = []
	for chain_index: int in chain:
		inputs.append_array(_edge_inputs[chain_index])
		actions.append(_edge_actions[chain_index])
	var replay: GBNavModel = _states[0].copy()
	var trajectory: Array[Vector2] = []
	for input: Array in inputs:
		replay.step(int(input[0]), int(input[1]) != 0)
		trajectory.append(replay.pos)
	return {
		"ok": true,
		"inputs": inputs,
		"trajectory": trajectory,
		"actions": actions,
		"ticks": inputs.size(),
		"expansions": expansions,
		"closed": closed_size,
		"budget_hit": false,
		"frontier_exhausted": false,
		"nearest_gap": 0.0,
		"plan_ms": plan_ms,
		"end": replay.pos,
		"end_ok": true,
	}


func _heuristic(model: GBNavModel) -> float:
	var speed: float = maxf(float(model.physics["run_speed"]), 1.0)
	var ticks_per_second: float = float(model.physics["ticks_per_second"])
	return _rect_gap(model.body_rect(), _goal_rect) / speed * ticks_per_second


func _reached(model: GBNavModel) -> bool:
	                                                                           
	                                                                             
	                                                                          
	return _goal_rect.grow(-0.5).has_point(model.body_rect().get_center())


static func _rect_gap(first: Rect2, second: Rect2) -> float:
	var dx: float = maxf(maxf(second.position.x - first.end.x, first.position.x - second.end.x), 0.0)
	var dy: float = maxf(maxf(second.position.y - first.end.y, first.position.y - second.end.y), 0.0)
	return Vector2(dx, dy).length()


func _push(priority: float, index: int) -> void:
	_heap.append([priority, index])
	var child: int = _heap.size() - 1
	while child > 0:
		var parent: int = (child - 1) >> 1
		if float(_heap[parent][0]) <= float(_heap[child][0]):
			break
		var temporary: Array = _heap[parent]
		_heap[parent] = _heap[child]
		_heap[child] = temporary
		child = parent


func _pop() -> int:
	var result: int = int(_heap[0][1])
	var last: Array = _heap.pop_back()
	if not _heap.is_empty():
		_heap[0] = last
		var parent := 0
		while true:
			var left := parent * 2 + 1
			var right := left + 1
			var smallest := parent
			if left < _heap.size() and float(_heap[left][0]) < float(_heap[smallest][0]):
				smallest = left
			if right < _heap.size() and float(_heap[right][0]) < float(_heap[smallest][0]):
				smallest = right
			if smallest == parent:
				break
			var temporary: Array = _heap[smallest]
			_heap[smallest] = _heap[parent]
			_heap[parent] = temporary
			parent = smallest
	return result
