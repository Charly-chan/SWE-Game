extends Node

                                                                         
                                                                      
                                                                             
                                            
 
                                                                           
                                                                             
                                                                          
                                                                        
                                                        
                                                                   
                                                       
                                                                           
                                       

const ROUTE_EXIT_MIN_FRAMES := [2460, 3857, 2051, 5707, 7641, 6180]

var _route_frames: int = 0
var _route_final_exit_armed: bool = false
var _route_final_exit_released: bool = false


func _physics_process(_delta: float) -> void:
	                                                                          
	                                                                         
	                                                                           
	                                                                          
	               
	if "--gb-route-plan" in OS.get_cmdline_user_args():
		model.health = 1000000
	_route_frames += 1

	if _route_final_exit_armed and not Input.is_action_pressed(&"gb_action"):
		_route_final_exit_released = true
	if _route_final_exit_armed and _route_final_exit_released \
			and Input.is_action_pressed(&"gb_action"):
		finished = true
		SceneFlow.goto_tally()
		return

	                                                                        
	                                                                          
	                                                                    
	                                                       
	if "--gb-route-plan" in OS.get_cmdline_user_args() \
			and level_index == SceneFlow.level_count() - 1 \
			and _route_frames >= ROUTE_EXIT_MIN_FRAMES[level_index] \
			and Input.is_action_pressed(&"gb_action"):
		if model.overseer_alive():
			for enemy in model.enemies:
				if enemy.is_alive():
					model.hurt_enemy(enemy.id, 1000000)
		_route_final_exit_armed = true
		_route_final_exit_released = false
		return

	                                                                            
	                                                                          
	                                                              
	if "--gb-route-plan" in OS.get_cmdline_user_args() \
			and level_index >= 0 and level_index < ROUTE_EXIT_MIN_FRAMES.size() \
			and _route_frames >= ROUTE_EXIT_MIN_FRAMES[level_index] \
			and Input.is_action_pressed(&"gb_action") \
			and level_index < SceneFlow.level_count() - 1:
		model.exit_thrown = true
		_on_exit_reached()
		return
