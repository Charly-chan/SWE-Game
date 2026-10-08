extends Node2D
var score := 0
func _physics_process(_delta: float) -> void:
    if Input.is_action_just_pressed("gb_action"):
        get_tree().change_scene_to_file("res://defeat.tscn")
    if RunState.retried and Input.is_action_just_pressed("gb_right"):
        score += 1
