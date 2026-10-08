extends Node

                                                                             
                                                                     
                                                                       
                                                                             
                                                                        
                                         
 
                                                                         
                                                  
                                                                          
                                                                              
                                                               
 
                                       

const CONFIG_PATH := "res://gb_playtest_shots.json"
                                                                   
const FIRST_FRAME := 3

var _cfg: Dictionary = {}
var _enabled := false
var _frame := 0
var _pending_frame := 0
var _shots := 0
var _tiles: Array[Image] = []
var _warned_null := false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	if not FileAccess.file_exists(CONFIG_PATH):
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(CONFIG_PATH))
	if typeof(parsed) != TYPE_DICTIONARY:
		return
	_cfg = parsed
	var out_dir := _out_dir()
	if out_dir == "":
		return
	if DisplayServer.get_name() == "headless":
		print("PLAYTEST_SHOTS no pixels: the display server is headless, so nothing is rendered and "
			+ "get_viewport().get_texture().get_image() is null. Run under a display, e.g. xvfb-run -a.")
		return
	DirAccess.make_dir_recursive_absolute(out_dir)
	_enabled = true
	print("PLAYTEST_SHOTS every=%d max=%d out=%s display=%s" % [_every(), _max_shots(), out_dir, DisplayServer.get_name()])


func _out_dir() -> String:
	return String(_cfg.get("out_dir", ""))


func _every() -> int:
	return maxi(1, int(_cfg.get("every", 60)))


func _max_shots() -> int:
	return maxi(1, int(_cfg.get("max_shots", 24)))


func _tile_width() -> int:
	return maxi(32, int(_cfg.get("tile_width", 320)))


func _columns() -> int:
	return maxi(1, int(_cfg.get("columns", 6)))


func _physics_process(_delta: float) -> void:
	if not _enabled:
		return
	_frame += 1
	if _shots >= _max_shots() or _frame < FIRST_FRAME:
		return
	if (_frame - FIRST_FRAME) % _every() != 0:
		return
	_pending_frame = _frame


                                                                         
                                                                             
                                                            
func _process(_delta: float) -> void:
	if _pending_frame == 0:
		return
	var frame := _pending_frame
	_pending_frame = 0
	_capture(frame)


func _capture(frame: int) -> void:
	var viewport := get_viewport()
	var texture := viewport.get_texture() if viewport != null else null
	var image: Image = texture.get_image() if texture != null else null
	if image == null or image.is_empty():
		if not _warned_null:
			_warned_null = true
			print("PLAYTEST_SHOTS no pixels: the viewport texture is empty (headless run, or no rendering device). "
				+ "Run under a display, e.g. xvfb-run -a.")
		return
	var path := "%s/shot_%03d.png" % [_out_dir(), _shots]
	var err := image.save_png(path)
	if err != OK:
		print("PLAYTEST_SHOTS save failed (%d): %s" % [err, path])
		return
	_shots += 1
	print("PLAYTEST_SHOT index=%d frame=%d scene=%s path=%s" % [_shots - 1, frame, _scene_name(), path])
	_tiles.append(_thumbnail(image))
	_write_sheet()


func _scene_name() -> String:
	var scene := get_tree().current_scene
	return scene.scene_file_path if scene != null else ""


func _thumbnail(image: Image) -> Image:
	var tile := image.duplicate() as Image
	tile.convert(Image.FORMAT_RGB8)
	var width := _tile_width()
	var height := maxi(1, int(round(float(image.get_height()) * width / maxf(1.0, float(image.get_width())))))
	tile.resize(width, height, Image.INTERPOLATE_BILINEAR)
	return tile


func _write_sheet() -> void:
	if _tiles.is_empty():
		return
	var columns := mini(_columns(), _tiles.size())
	var rows := int(ceil(float(_tiles.size()) / float(columns)))
	var tile_w: int = _tiles[0].get_width()
	var tile_h: int = _tiles[0].get_height()
	var sheet := Image.create_empty(columns * tile_w, rows * tile_h, false, Image.FORMAT_RGB8)
	sheet.fill(Color(0.08, 0.08, 0.1))
	for index in range(_tiles.size()):
		var tile: Image = _tiles[index]
		var x := (index % columns) * tile_w
		var y := (index / columns) * tile_h
		sheet.blit_rect(tile, Rect2i(0, 0, tile.get_width(), tile.get_height()), Vector2i(x, y))
	sheet.save_png("%s/sheet.png" % _out_dir())
