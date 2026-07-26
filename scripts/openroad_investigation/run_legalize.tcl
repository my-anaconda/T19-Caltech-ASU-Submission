read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef
read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef
read_def /tmp2/block1.def

puts "=== BEFORE legalization: check_placement ==="
if {[catch {check_placement -verbose} result]} {
    puts "check_placement raised: $result"
} else {
    puts "check_placement result: $result"
}

puts "=== Running detailed_placement (max_displacement 2) ==="
detailed_placement -max_displacement 2

puts "=== Filling gaps left by moved cells (filler_placement) ==="
filler_placement {FILLER_ASAP7_75t_R FILLERxp5_ASAP7_75t_R}

puts "=== AFTER legalization: check_placement ==="
if {[catch {check_placement -verbose} result2]} {
    puts "check_placement raised: $result2"
} else {
    puts "check_placement result: $result2"
}

set block [ord::get_db_block]
set insts [$block getInsts]
set moved 0
set total 0
foreach inst $insts {
    incr total
}
puts "Total instances in design: $total"

write_def /tmp2/block1_legalized.def
puts "Wrote legalized DEF"
