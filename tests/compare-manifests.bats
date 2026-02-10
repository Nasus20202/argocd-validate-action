#!/usr/bin/env bats

setup() {
    TEST_DIR=$(mktemp -d)
    STATE_DIR="$TEST_DIR/state"
    MANIFESTS_DIR="$TEST_DIR/manifests"
    export GITHUB_OUTPUT="$TEST_DIR/github_output"
    touch "$GITHUB_OUTPUT"

    SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    COMPARE_SCRIPT="$SCRIPT_DIR/compare-manifests.sh"
}

teardown() {
    rm -rf "$TEST_DIR"
}

@test "initializes state directory when it does not exist" {
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Initializing"* ]] || [[ "$output" == *"initializing"* ]] || [[ "$output" == *"initialized"* ]]
    [ -f "$STATE_DIR/app1/deployment.yaml" ]
    grep -q "DIFF_STATUS=initialized" "$GITHUB_OUTPUT"
}

@test "initializes state directory when it is empty" {
    mkdir -p "$STATE_DIR"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    [ -f "$STATE_DIR/app1/deployment.yaml" ]
    grep -q "DIFF_STATUS=initialized" "$GITHUB_OUTPUT"
}

@test "detects no changes when manifests are identical" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: Deployment" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    [[ "$output" == *"No changes detected"* ]]
    grep -q "DIFF_STATUS=unchanged" "$GITHUB_OUTPUT"
}

@test "detects changes when manifests differ" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: StatefulSet" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Changes detected"* ]]
    grep -q "DIFF_STATUS=changed" "$GITHUB_OUTPUT"
    grep -q "Modified: 1" "$GITHUB_OUTPUT"
}

@test "detects added files" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: Deployment" > "$MANIFESTS_DIR/app1/deployment.yaml"
    echo "kind: Service" > "$MANIFESTS_DIR/app1/service.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    grep -q "DIFF_STATUS=changed" "$GITHUB_OUTPUT"
    grep -q "Added: 1" "$GITHUB_OUTPUT"
}

@test "detects removed files" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: Service" > "$STATE_DIR/app1/service.yaml"
    echo "kind: Deployment" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    grep -q "DIFF_STATUS=changed" "$GITHUB_OUTPUT"
    grep -q "Removed: 1" "$GITHUB_OUTPUT"
}

@test "updates state directory after comparison" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: StatefulSet" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    # State should now match manifests
    diff -q "$STATE_DIR/app1/deployment.yaml" "$MANIFESTS_DIR/app1/deployment.yaml"
}

@test "fails when manifests directory does not exist" {
    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$TEST_DIR/nonexistent"

    [ "$status" -ne 0 ]
    [[ "$output" == *"does not exist"* ]]
}

@test "creates diff comment file when changes are detected" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: StatefulSet" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    COMMENT_FILE=$(grep "DIFF_COMMENT_FILE=" "$GITHUB_OUTPUT" | cut -d= -f2)
    [ -n "$COMMENT_FILE" ]
    [ -f "$COMMENT_FILE" ]
    grep -q "ArgoCD Manifest Changes" "$COMMENT_FILE"
    grep -q "Modified" "$COMMENT_FILE"
}

@test "handles new subdirectories in manifests" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app1"
    mkdir -p "$MANIFESTS_DIR/app2"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: Deployment" > "$MANIFESTS_DIR/app1/deployment.yaml"
    echo "kind: Service" > "$MANIFESTS_DIR/app2/service.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    grep -q "DIFF_STATUS=changed" "$GITHUB_OUTPUT"
}

@test "handles removed subdirectories from manifests" {
    mkdir -p "$STATE_DIR/app1"
    mkdir -p "$STATE_DIR/app2"
    mkdir -p "$MANIFESTS_DIR/app1"
    echo "kind: Deployment" > "$STATE_DIR/app1/deployment.yaml"
    echo "kind: Service" > "$STATE_DIR/app2/service.yaml"
    echo "kind: Deployment" > "$MANIFESTS_DIR/app1/deployment.yaml"

    run bash "$COMPARE_SCRIPT" "$STATE_DIR" "$MANIFESTS_DIR"

    [ "$status" -eq 0 ]
    grep -q "DIFF_STATUS=changed" "$GITHUB_OUTPUT"
}
