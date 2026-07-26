read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef
read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef
read_def /tmp2/block1_routable.def

puts "=== Creating routing tracks (ASAP7 reference make_tracks.tcl) ==="
source /OpenROAD-flow-scripts/flow/platforms/asap7/openRoad/make_tracks.tcl

puts "=== Setting routing layers (M2-M7, matching ASAP7 config.mk default) ==="
set_routing_layers -signal M2-M7

puts "=== Design loaded, running global_route ==="
if {[catch {global_route -guide_file /tmp2/route.guide} result]} {
    puts "global_route FAILED: $result"
} else {
    puts "global_route OK"
}

puts "=== Running detailed_route ==="
if {[catch {detailed_route -output_drc /tmp2/route_drc.rpt} result2]} {
    puts "detailed_route FAILED: $result2"
} else {
    puts "detailed_route OK"
}

write_def /tmp2/block1_fully_routed.def
puts "Wrote routed DEF"
